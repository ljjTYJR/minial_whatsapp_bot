#!/usr/bin/env python3
"""OAK-D left/right mono camera visualization using DepthAI v3 API."""
import cv2
import depthai as dai


def main():
    p = dai.Pipeline()

    left  = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_B)
    right = p.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_C)

    left_q  = left.requestOutput((640, 400), dai.ImgFrame.Type.BGR888p).createOutputQueue()
    right_q = right.requestOutput((640, 400), dai.ImgFrame.Type.BGR888p).createOutputQueue()

    p.start()
    print("Streaming OAK-D left/right mono — press 'q' to quit")
    try:
        while p.isRunning():
            cv2.imshow("Left",  left_q.get().getCvFrame())
            cv2.imshow("Right", right_q.get().getCvFrame())
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        p.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
