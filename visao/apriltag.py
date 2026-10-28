import numpy as np
import cv2 as cv
from pupil_apriltags import Detector
import os
import math

# Cross-platform function to clear the console
def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

at_detector = Detector(
   families="tag25h9",
   nthreads=1,
   quad_decimate=1.0,
   quad_sigma=0.0,
   refine_edges=1,
   decode_sharpening=0.25,
   debug=0
)

## undistort
mtx = np.array([[840.33494173, 0.,         261.637177],
[  0.,         849.23425196, 165.6552175 ],
[  0.,           0.,           1.        ]])

dist = np.zeros([4, 1])

cap = cv.VideoCapture(0)
if not cap.isOpened():
    print("Cannot open camera")
    exit()
while True:
    # Capture frame-by-frame
    ret, frame = cap.read()

    dst = cv.undistort(frame, mtx, dist)

    # if frame is read correctly ret is True
    if not ret:
        print("Can't receive frame (stream end?). Exiting ...")
        break
    # Our operations on the frame come here
    gray = cv.cvtColor(dst, cv.COLOR_BGR2GRAY)
    detectado = at_detector.detect(img= gray, camera_params = [840.33494173, 849.23425196, 261.637177, 165.6552175], estimate_tag_pose = True, tag_size=0.004)
    # results = at_detector.detect(gray)
    if detectado != []:
        clear_console()
        # print(detectado)

        for result in detectado:
            distancia = ((result.pose_t[0][0]**2 + result.pose_t[1][0]**2 + result.pose_t[2][0]**2)**(1/2))
            print("Distancia é:" + str(distancia*1000))

            (corner_a, corner_b, corner_c, corner_d) = result.corners

            corner_a = (int(corner_a[0]), int(corner_a[1]))
            #print(corner_a)
            corner_b = (int(corner_b[0]), int(corner_b[1]))
            #print(corner_b)
            corner_c = (int(corner_c[0]), int(corner_c[1]))
            #print(corner_c)
            corner_d = (int(corner_d[0]), int(corner_d[1]))
            #print(corner_d)

            line_color = (0, 255, 0) # green
            line_thickness = 2

            # Draw a rectangle using the corners
            cv.line(frame, corner_a, corner_b, line_color, line_thickness)
            cv.line(frame, corner_b, corner_c, line_color, line_thickness)
            cv.line(frame, corner_c, corner_d, line_color, line_thickness)
            cv.line(frame, corner_d, corner_a, line_color, line_thickness)


            
    # Display the resulting frame
    cv.imshow('frame', frame)
    if cv.waitKey(1) == ord('q'):
        break

# When everything done, release the capture
cap.release()
cv.destroyAllWindows()