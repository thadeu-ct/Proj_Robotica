from ultralytics import YOLO
import cv2
import frame from apriltag.py

model = YOLO("yolo26n.pt")
cap = cv2.VideoCapture(0)

model.predict(source=0, show=True, conf=0.6)
results[0].show()  # Display results

while True:
    ret, frame = cap.read()
    if not ret:
        print("Error: Cannot read frame.")
        break

    # Run YOLO detection on the frame
    results = model.predict(source=frame, show=False, conf=0.5)

    # Visualize detections on the frame
    annotated_frame = results[0].plot()

    # Display the frame with detections
    cv2.imshow('YOLO Webcam', annotated_frame)

    # Exit on pressing 'q'
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
