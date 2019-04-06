from utils import detector_utils as detector_utils
from utils import ncs2_utils as ncs2
from utils import pose_classification_utils as classifier
import cv2
import tensorflow as tf
import multiprocessing
from multiprocessing import Queue, Pool
import time
from utils.detector_utils import WebcamVideoStream
import datetime
import argparse
import os; 
os.environ['KERAS_BACKEND'] = 'tensorflow'
import keras
import gui

frame_processed = 0
score_thresh = 0.18

# Create a worker thread that loads graph and
# does detection on images in an input queue and puts it on an output queue


def worker(input_q, output_q, cropped_output_q, inferences_q, cap_params, frame_processed):
    print(">> loading frozen model for worker")
    detection_graph, sess = detector_utils.load_inference_graph()
    sess = tf.Session(graph=detection_graph)

    print(">> loading keras model for worker")
    try:
        model, classification_graph, session = classifier.load_KerasGraph("cnn/models/KERAS/hand_poses_wGarbage_10.h5")
    except Exception as e:
        print(e)

    while True:
        #print("> ===== in worker loop, frame ", frame_processed)
        frame = input_q.get()
        if (frame is not None):
            # Actual detection. Variable boxes contains the bounding box cordinates for hands detected,
            # while scores contains the confidence for each of these boxes.
            # Hint: If len(boxes) > 1 , you may assume you have found atleast one hand (within your score threshold)
            boxes, scores = detector_utils.detect_objects(
                frame, detection_graph, sess)

            # get region of interest
            res = detector_utils.get_box_image(cap_params['num_hands_detect'], cap_params["score_thresh"],
                scores, boxes, cap_params['im_width'], cap_params['im_height'], frame)
        
            # draw bounding boxes
            detector_utils.draw_box_on_image(cap_params['num_hands_detect'], cap_params["score_thresh"],
                scores, boxes, cap_params['im_width'], cap_params['im_height'], frame)
            
            # classify hand pose
            if res is not None and inferences_q is not None:
                class_res = classifier.classify(model, classification_graph, session, res)
                inferences_q.put(class_res)       
            
            # add frame annotated with bounding box to queue
            cropped_output_q.put(res)
            output_q.put(frame)
            frame_processed += 1
        else:
            output_q.put(frame)
    sess.close()


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-src',
        '--source',
        dest='video_source',
        type=int,
        default=0,
        help='Device index of the camera.')
    parser.add_argument(
        '-nhands',
        '--num_hands',
        dest='num_hands',
        type=int,
        default=1,
        help='Max number of hands to detect.')
    parser.add_argument(
        '-fps',
        '--fps',
        dest='fps',
        type=int,
        default=1,
        help='Show FPS on detection/display visualization')
    parser.add_argument(
        '-wd',
        '--width',
        dest='width',
        type=int,
        default=300,
        help='Width of the frames in the video stream.')
    parser.add_argument(
        '-ht',
        '--height',
        dest='height',
        type=int,
        default=200,
        help='Height of the frames in the video stream.')
    parser.add_argument(
        '-ds',
        '--display',
        dest='display',
        type=int,
        default=1,
        help='Display the detected images using OpenCV. This reduces FPS')
    parser.add_argument(
        '-num-w',
        '--num-workers',
        dest='num_workers',
        type=int,
        default=4,
        help='Number of workers.')
    parser.add_argument(
        '-q-size',
        '--queue-size',
        dest='queue_size',
        type=int,
        default=5,
        help='Size of the queue.')
    parser.add_argument(
        '-ncs2',
        '--ncs2',
        dest='ncs2',
        type=bool,
        default=True,
        help='Run inferences on the NCS2')
    parser.add_argument(
        '-bin',
        '--bin_path',
        dest='bin_path',
        type=str,
        default='cnn/models/IR/IR_model.bin',
        help='IR bin file')
    parser.add_argument(
        '-xml',
        '--xml_path',
        dest='xml_path',
        type=str,
        default='cnn/models/IR/IR_model.xml',
        help='IR XML file')
    args = parser.parse_args()

    input_q             = Queue(maxsize=args.queue_size)
    output_q            = Queue(maxsize=args.queue_size)
    cropped_output_q    = Queue(maxsize=args.queue_size)
    if not args.ncs2:
        _inferences_q        = Queue(maxsize=args.queue_size)
    else:
        net = ncs2.readIRModels(args.bin_path, args.xml_path)
        exec_net = ncs2.loadToDevice(net, _device="MYRIAD")
        input_blob = next(iter(net.inputs))
        output_blob = next(iter(net.outputs))
        del net

    video_capture = WebcamVideoStream(
        src=args.video_source, width=args.width, height=args.height).start()

    cap_params = {}
    frame_processed = 0
    cap_params['im_width'], cap_params['im_height'] = video_capture.size()
    print(cap_params['im_width'], cap_params['im_height'])
    cap_params['score_thresh'] = score_thresh

    # max number of hands we want to detect/track
    cap_params['num_hands_detect'] = args.num_hands

    print(cap_params, args)
    
    # Count number of files to increment new example directory
    poses = []
    _file = open("poses.txt", "r") 
    lines = _file.readlines()
    for line in lines:
        line = line.strip()
        if(line != ""):
            print(line)
            poses.append(line)


    # spin up workers to parallelize detection.
    if ncs2:
        pool = Pool(args.num_workers, worker,
                (input_q, output_q, cropped_output_q, None, cap_params, frame_processed))
    else:
        pool = Pool(args.num_workers, worker,
                (input_q, output_q, cropped_output_q, _inferences_q, cap_params, frame_processed))

    start_time = datetime.datetime.now()
    num_frames = 0
    fps = 0
    index = 0

    cv2.namedWindow('Handpose', cv2.WINDOW_NORMAL)

    while True:
        frame = video_capture.read()
        frame = cv2.flip(frame, 1)
        index += 1

        input_q.put(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        output_frame = output_q.get()
        cropped_output = cropped_output_q.get()

        inferences      = None
        if not args.ncs2:
            try:
                inferences = _inferences_q.get_nowait()
            except Exception as e:
                pass   
        else:
            prepimg = ncs2.prepareImage(cropped_output, input_blob)
            inferences = ncs2.infer(exec_net, input_blob, prepimg)[output_blob]

        elapsed_time = (datetime.datetime.now() - start_time).total_seconds()
        num_frames += 1
        fps = num_frames / elapsed_time

        # Display inferences
        if(inferences is not None):
            gui.drawInferences(inferences, poses)

        if (cropped_output is not None):
            cropped_output = cv2.cvtColor(cropped_output, cv2.COLOR_RGB2BGR)
            if (args.display > 0):
                cv2.namedWindow('Cropped', cv2.WINDOW_NORMAL)
                cv2.resizeWindow('Cropped', 450, 300)
                cv2.imshow('Cropped', cropped_output)
                #cv2.imwrite('image_' + str(num_frames) + '.png', cropped_output)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                if (num_frames == 400):
                    num_frames = 0
                    start_time = datetime.datetime.now()
                else:
                    print("frames processed: ", index, "elapsed time: ",
                          elapsed_time, "fps: ", str(int(fps)))

    
        # print("frame ",  index, num_frames, elapsed_time, fps)

        if (output_frame is not None):
            output_frame = cv2.cvtColor(output_frame, cv2.COLOR_RGB2BGR)
            if (args.display > 0):
                if (args.fps > 0):
                    detector_utils.draw_fps_on_image("FPS : " + str(int(fps)),
                                                     output_frame)
                cv2.imshow('Handpose', output_frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            else:
                if (num_frames == 400):
                    num_frames = 0
                    start_time = datetime.datetime.now()
                else:
                    print("frames processed: ", index, "elapsed time: ",
                          elapsed_time, "fps: ", str(int(fps)))
        else:
            print("video end")
            break
    elapsed_time = (datetime.datetime.now() - start_time).total_seconds()
    fps = num_frames / elapsed_time
    print("fps", fps)
    pool.terminate()
    video_capture.stop()
    cv2.destroyAllWindows()
