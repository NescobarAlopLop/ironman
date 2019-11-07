import argparse
import threading
import time

import cv2
import numpy as np
import pygame
from flask import Flask
from flask import Response
from flask import render_template
from pygame.locals import *

from djitellopy import Tello

# Speed of the drone
S = 60
# Frames per second of the pygame window display
FPS = 25

global_frame = None
lock = threading.Lock()
should_stop = False


class FrontEnd(object):
    """ Maintains the Tello display and moves it through the keyboard keys.
        Press escape key to quit.
        The controls are:
            - T: Takeoff
            - L: Land
            - Arrow keys: Forward, backward, left and right.
            - A and D: Counter clockwise and clockwise rotations
            - W and S: Up and down.
    """

    def __init__(self):
        # Init pygame
        pygame.init()

        # Creat pygame window
        pygame.display.set_caption("Tello video stream")
        self.screen = pygame.display.set_mode([960, 720])

        # Init Tello object that interacts with the Tello drone
        self.tello = Tello()

        # Drone velocities between -100~100
        self.for_back_velocity = 0
        self.left_right_velocity = 0
        self.up_down_velocity = 0
        self.yaw_velocity = 0
        self.speed = 10

        self.send_rc_control = False

        # create update timer
        pygame.time.set_timer(USEREVENT + 1, 50)

    def run(self):
        global global_frame, lock
        if not self.tello.connect():
            print("Tello not connected")
            return

        if not self.tello.set_speed(self.speed):
            print("Not set speed to lowest possible")
            return

        # In case streaming is on. This happens when we quit this program without the escape key.
        if not self.tello.streamoff():
            print("Could not stop video stream")
            return

        # send stream on command
        if not self.tello.streamon():
            print("Could not start video stream")
            return

        frame_read = self.tello.get_frame_read()
        global should_stop
        should_stop = False
        while not should_stop:

            for event in pygame.event.get():
                if event.type == USEREVENT + 1:
                    self.update()
                elif event.type == QUIT:
                    should_stop = True
                elif event.type == KEYDOWN:
                    if event.key == K_ESCAPE:
                        should_stop = True
                    else:
                        self.keydown(event.key)
                elif event.type == KEYUP:
                    self.key_up(event.key)

            if frame_read.stopped:
                frame_read.stop()
                break

            self.screen.fill([0, 0, 200])
            frame = cv2.cvtColor(frame_read.frame, cv2.COLOR_BGR2RGB)
            with lock:
                global_frame = frame_read.frame.copy()
            frame = np.rot90(frame)
            frame = np.flipud(frame)

            frame = pygame.surfarray.make_surface(frame)
            self.screen.blit(frame, (0, 0))
            pygame.display.update()

            time.sleep(1 / FPS)

        # Call it always before finishing. I deallocate resources.
        self.tello.end()

    def keydown(self, key):
        """ Update velocities based on key pressed
        Arguments:
            key: pygame key
        """
        if key == pygame.K_UP:  # set forward velocity
            self.for_back_velocity = S
        elif key == pygame.K_DOWN:  # set backward velocity
            self.for_back_velocity = -S
        elif key == pygame.K_LEFT:  # set left velocity
            self.left_right_velocity = -S
        elif key == pygame.K_RIGHT:  # set right velocity
            self.left_right_velocity = S
        elif key == pygame.K_w:  # set up velocity
            self.up_down_velocity = S
        elif key == pygame.K_s:  # set down velocity
            self.up_down_velocity = -S
        elif key == pygame.K_a:  # set yaw clockwise velocity
            self.yaw_velocity = -S
        elif key == pygame.K_d:  # set yaw counter clockwise velocity
            self.yaw_velocity = S

    def key_up(self, key):
        """ Update velocities based on key released
        Arguments:
            key: pygame key
        """
        try:
            if key == pygame.K_UP or key == pygame.K_DOWN:  # set zero forward/backward velocity
                self.for_back_velocity = 0
            elif key == pygame.K_LEFT or key == pygame.K_RIGHT:  # set zero left/right velocity
                self.left_right_velocity = 0
            elif key == pygame.K_w or key == pygame.K_s:  # set zero up/down velocity
                self.up_down_velocity = 0
            elif key == pygame.K_a or key == pygame.K_d:  # set zero yaw velocity
                self.yaw_velocity = 0
            elif key == pygame.K_t:  # takeoff
                self.tello.takeoff()
                self.send_rc_control = True
            elif key == pygame.K_l:  # land
                self.tello.land()
                self.send_rc_control = False
            elif key == pygame.K_y:
                self.tello.get_temperature()
            elif key == pygame.K_b:
                self.tello.get_battery()
        except Exception as e:
            print(e)

    def update(self):
        """ Update routine. Send velocities to Tello."""
        if self.send_rc_control:
            self.tello.send_rc_control(self.left_right_velocity, self.for_back_velocity, self.up_down_velocity,
                                       self.yaw_velocity)


# initialize a flask object
app = Flask(__name__)


@app.route("/")
def index():
    # return the rendered template
    return render_template("index.html")


def generate():
    # grab global references to the output frame and lock variables
    global global_frame, lock

    # loop over frames from the output stream
    while True:
        # wait until the lock is acquired
        with lock:
            # check if the output frame is available, otherwise skip
            # the iteration of the loop
            if global_frame is None:
                continue

            # encode the frame in JPEG format
            (flag, encodedImage) = cv2.imencode(".jpg", global_frame)

            # ensure the frame was successfully encoded
            if not flag:
                continue

        # yield the output frame in the byte format
        yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')


@app.route("/video_feed.mjpg")
def video_feed():
    # return the response generated along with the specific media
    # type (mime type)
    return Response(generate(), mimetype = "multipart/x-mixed-replace; boundary=frame")


def tello_thread():
    frontend = FrontEnd()
    frontend.run()


if __name__ == '__main__':
    # construct the argument parser and parse command line arguments
    ap = argparse.ArgumentParser()
    # ap.add_argument("-i", "--ip", type=str, default='0.0.0.0',
    ap.add_argument("-i", "--ip", type=str, default='localhost',
                    help="ip address of the device")
    ap.add_argument("-o", "--port", type=int, default=8080,
                    help="ephemeral port number of the server (1024 to 65535)")
    ap.add_argument("-f", "--frame-count", type=int, default=32,
                    help="# of frames used to construct the background model")
    args = vars(ap.parse_args())
    try:
        # start a thread that will perform motion detection
        t = threading.Thread(target=tello_thread)  # , args=(args["frame_count"],))
        t.daemon = True
        t.start()

        # start the flask app
        app.run(host=args["ip"], port=args["port"], debug=False,
                threaded=True, use_reloader=False)
    except KeyboardInterrupt:

        should_stop = True
        t.join()
