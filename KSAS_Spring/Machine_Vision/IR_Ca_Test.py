#! /usr/bin/env python3.6

import cv2
import time
import numpy
import math
import copy
import rospy
from std_msgs.msg import Float64MultiArray
from std_msgs.msg import MultiArrayDimension
from std_msgs.msg import Int8


class Vertex():

    def __init__(self, pub):

        self.pub = pub
        self.sub = rospy.Subscriber('arduino', Int8, self.arduino_cb)
        self.fourcc = cv2.VideoWriter_fourcc(*'DIVX')
        self.out = cv2.VideoWriter('output.avi', self.fourcc, 15.0, (640, 480))
        self.light_OFF_C2 = 0
        self.light_ON_C3 = 0
        self.system_mat = Float64MultiArray()
        self.Dimension = MultiArrayDimension()
        self.Dimension.size = 7
        self.Dimension.stride = 1
        self.Dimension.label = "Info"
        self.system_mat.layout.dim.append(self.Dimension)
        self.ret_pre = 0
        self.ret = 0
        self.Controller = 0
        self.DoD = False
        self.Detect = False
        self.arrival = False
        self.objs = numpy.array([[-0.025, 0.025, 0.0], [0.025, 0.025, 0.0], [0.025, -0.025, 0.0], [-0.025, -0.025, 0.0]])
        self.objs_cube = numpy.float32([[-0.025, 0.025, 0.0], [0.025, 0.025, 0.0], [0.025, -0.025, 0.0], [-0.025, -0.025, 0.0], 
                        [-0.025, 0.025, -0.05], [0.025, 0.025, -0.05], [0.025, -0.025, -0.05], [-0.025, -0.025, -0.05]])
        self.prev_centroids = numpy.array([[0, 0], [0, 0], [0, 0], [0, 0]], dtype=numpy.float32)
        self.str_mode = "Search , Mode 0"
        self.time_per_frame_video = 1 / 15
        self.last_time = time.perf_counter()
        self.R_flip = numpy.zeros((3, 3), dtype=numpy.float32)
        self.R_flip[0, 0] = 1.0
        self.R_flip[1, 1] = -1.0
        self.R_flip[2, 2] = -1.0
        self.rvec = numpy.array([[0], [0], [0]])
        self.tvec = numpy.array([[0], [0], [0]])
        self.Roll = 0
        self.Pitch = 0
        self.Yaw = 0
        self.mtx = numpy.array([[631.546, 0, 335.197], [0, 632.597, 243.093], [0, 0, 1]])
        self.distort_mtx = numpy.array([[-0.13804, 0.23296, -0.00048708, -0.00029049]]) # k1, k2, p1, p2, k3


    def Processing(self, frametmp):

        self.Initiator()

        frame = self.Command(frametmp)

        self.putTexts(frame)

        cv2.imshow("IR Image", frame)

        self.out.write(frame)

        self.system_mat.data = [self.Controller, self.tvec[0], self.tvec[1], self.tvec[2], self.Roll, self.Pitch, self.Yaw]

        self.pub.publish(self.system_mat)


    def arduino_cb(self, data):

        if data.data == 0:

            self.light_OFF_C2 = 1

        elif data.data == 1:

            self.light_ON_C3 = 1


    def isRotationMatrix(self, R):

        Rt = numpy.transpose(R)
        Identity = numpy.dot(Rt, R)
        I = numpy.identity(3, dtype=R.dtype)
        n = numpy.linalg.norm(I - Identity)

        return n < 1e-6  # Error

    def rotationMat2EulerAngle(self, R):

        assert (self.isRotationMatrix(R))

        sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])

        singular = sy < 1e-6

        if not singular:
            x = math.atan2(R[2, 1], R[2, 2])
            y = math.atan2(-R[2, 0], sy)
            z = math.atan2(R[1, 0], R[0, 0])
        else:
            x = math.atan2(-R[1, 2], R[1, 1])
            y = math.atan2(-R[2, 0], sy)
            z = 0

        return numpy.array([x, y, z])


    def Switching(self, centroids, criterion):

        if criterion[1][0] > criterion[2][0]:

            temp2 = copy.deepcopy(centroids[2])

            centroids[2] = centroids[1]

            centroids[1] = temp2

        if self.ret == 5:

            if criterion[4][0] > criterion[3][0]:

                temp = copy.deepcopy(centroids[3])

                centroids[3] = centroids[4]

                centroids[4] = temp

        return centroids


    def Fixed_Switching(self, cur_cent):

        for i in range(1, self.ret):

            dist = []

            for idx in range(i, self.ret):

                dist.append(numpy.linalg.norm(self.prev_centroids[i] - cur_cent[idx]))

            position = numpy.argmin(dist)

            temp = copy.deepcopy(cur_cent[i])

            cur_cent[i] = cur_cent[position+i]

            cur_cent[position+i] = temp

        return cur_cent


    def Watershed(self, img):

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # img_blue, img_green, img_red = cv2.split(img)
        ret, thresholded = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY)
        # ret, thresholded = cv2.threshold(gray, 250, 255, cv2.THRESH_BINARY)
        cv2.imshow("Tresholded Image", thresholded)
        kernel = numpy.ones((3, 3), dtype=numpy.uint8)
        Opening = cv2.morphologyEx(thresholded, cv2.MORPH_OPEN, kernel, iterations=2)
        sure_bg = cv2.dilate(Opening, kernel, iterations=3)
        dist_trans = cv2.distanceTransform(Opening, cv2.DIST_L2, 5)
        ret3, sure_fg = cv2.threshold(dist_trans, 0.5*dist_trans.max(), 255, 0)
        sure_fg = numpy.uint8(sure_fg)
        unknown = cv2.subtract(sure_bg, sure_fg)
        self.ret, labels, stats, centroids = cv2.connectedComponentsWithStats(sure_fg)
        labels = labels + 1
        labels[unknown == 255] = 0
        labels = cv2.watershed(img, labels)
        img[labels == -1] = [255, 0, 0]

        return img, centroids


    def Relative_Coord(self, img, centroids):

        corners = numpy.zeros((1, 4, 2), dtype=numpy.float32)

        for i in range(1, self.ret):

            (X, Y) = centroids[i]
            corners[0][i-1][0], corners[0][i-1][1] = X, Y
            cv2.putText(img, str(i), (math.floor(X), math.floor(Y)), cv2.FONT_HERSHEY_DUPLEX, 2, (0, 0, 255), 2)

        ret2, temp_rvec, temp_tvec = cv2.solvePnP(self.objs, corners, self.mtx, self.distort_mtx)
        self.rvec = numpy.array([temp_rvec[0][0], temp_rvec[1][0], temp_rvec[2][0]])
        self.tvec = numpy.array([temp_tvec[0][0], temp_tvec[1][0], temp_tvec[2][0]])

        self.prev_centroids = centroids

        if self.DoD == True:

            try :
                imgpts, jacobian = cv2.projectPoints(self.objs_cube, self.rvec, self.tvec, self.mtx, self.distort_mtx)
                img = self.draw_cube(img, imgpts)
                str_Position = "Position X=%0.4f Y=%0.4f Z=%0.4f" % (self.tvec[0], self.tvec[1], self.tvec[2])
                cv2.putText(img, str_Position, (10, 60), cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
                R_ct = numpy.matrix(cv2.Rodrigues(self.rvec)[0])
                R_tc = R_ct.T
                Pitch, Yaw, Roll = self.rotationMat2EulerAngle(R_tc * self.R_flip)
                self.Roll, self.Pitch, self.Yaw = math.degrees(Roll), math.degrees(Pitch), math.degrees(Yaw)
                str_Attitude = "Attitude R=%0.4f P=%0.4f Y=%0.4f" % (self.Roll, self.Pitch, self.Yaw)
                cv2.putText(img, str_Attitude, (10, 90), cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

            except Exception as e:
                rospy.loginfo(e)

        return img


    def Command(self, img):

        img, centroids = self.Watershed(img)

        if self.Controller == 0:

            if self.ret == 5:

                centroids = self.Switching(centroids, centroids)

                img = self.Relative_Coord(img, centroids)

        elif self.Controller == 1:

            if self.ret == 5:

                self.ret_pre = self.ret

                centroids = self.Fixed_Switching(centroids)

                img = self.Relative_Coord(img, centroids)

                # if self.tvec[2] <= 0.3:

                #     self.arrival = True

        elif self.Controller == 2:

            if self.light_OFF_C2 == 1:

                if self.ret == 4 and self.ret_pre == 5:

                    # centroids = self.Switching(centroids, centroids)
                    case_deter = [1, 2, 3, 4]

                    for i in range(1, self.ret):
                        dist = []
                        for idx in range(i, self.ret_pre):
                            dist.append(numpy.linalg.norm(self.prev_centroids[idx] - centroids[i]))
                        case_deter.remove(i + numpy.argmin(dist))
                        rospy.loginfo(i + numpy.argmin(dist))

                    rospy.loginfo("Case : " + str(case_deter[0]) + ", Numbering with Clock-Wise") 
                    criterion = case_deter[0] - 1
                    reversed_centroids = numpy.zeros((5, 2), dtype=numpy.float64)

                    for idx in range(1, 5):
                        if idx + criterion <= 4 and idx + criterion >= 0:
                            reversed_centroids[idx][0] = self.prev_centroids[idx + criterion][0]
                            reversed_centroids[idx][1] = self.prev_centroids[idx + criterion][1]

                        elif idx + criterion > 4:
                            reversed_centroids[idx][0] = self.prev_centroids[idx + criterion - 4][0]
                            reversed_centroids[idx][1] = self.prev_centroids[idx + criterion - 4][1]

                        else:
                            rospy.loginfo("Err 2")

                    self.DoD = True

                    self.prev_centroids = reversed_centroids

                    rospy.loginfo("DoD Completed")

                    self.light_OFF_C2 == 0


            else : rospy.loginfo("Waiting for Light OFF Signal...")

        elif self.Controller == 3:

            if self.light_ON_C3 == 1:

                if self.ret == 5:

                    self.ret_pre = self.ret

                    centroids = self.Fixed_Switching(centroids)

                    img = self.Relative_Coord(img, centroids)

            else : rospy.loginfo("Waiting for Light ON Signal...")

        return img



    def draw_cube(self, img, imgpts):

        for idx in range(4):

            if idx != 3:
                img = cv2.line(img, tuple(imgpts[idx].ravel()), tuple(imgpts[idx+1].ravel()), (255, 0, 0), 5)
                img = cv2.line(img, tuple(imgpts[idx].ravel()), tuple(imgpts[idx+4].ravel()), (0, 255, 0), 5)
                img = cv2.line(img, tuple(imgpts[idx+4].ravel()), tuple(imgpts[idx+5].ravel()), (0, 0, 255), 5)
            else:
                img = cv2.line(img, tuple(imgpts[0].ravel()), tuple(imgpts[idx].ravel()), (255, 0, 0), 5)
                img = cv2.line(img, tuple(imgpts[4].ravel()), tuple(imgpts[idx+4].ravel()), (0, 0, 255), 5)
                img = cv2.line(img, tuple(imgpts[idx].ravel()), tuple(imgpts[idx+4].ravel()), (0, 255, 0), 5)

            cv2.putText(img, str(idx+5), (math.floor(imgpts[idx+4][0][0]), math.floor(imgpts[idx+4][0][1])), cv2.FONT_HERSHEY_DUPLEX, 2, (0, 0, 255), 2)

        return img


    def putTexts(self, img):

        cv2.putText(frame, self.str_mode, (10, 30), cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

        self.time_per_frame = time.perf_counter() - self.last_time
        self.time_sleep_frame = max(0, self.time_per_frame_video - self.time_per_frame)
        time.sleep(self.time_sleep_frame)
        real_fps = 1 / (time.perf_counter() - self.last_time)
        self.last_time = time.perf_counter()
        text = '%5f fps' % real_fps
        x, y = 400, 10
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 1
        font_thickness = 2
        text_color = (0, 255, 0)
        text_color_bg = (0, 0, 0)
        text_size, _ = cv2.getTextSize(text, font, font_scale, font_thickness)
        text_w, text_h = text_size
        offset = 5
        cv2.rectangle(img, (x-offset, y-offset), (x+text_w+offset, y+text_h+offset), text_color_bg, -1)
        cv2.putText(img, text, (x, y+text_h + font_scale - 1), font, font_scale, text_color, font_thickness)



    def Initiator(self):

        self.rvec = numpy.array([[0], [0], [0]])
        self.tvec = numpy.array([[0], [0], [0]])
        self.Roll = 0
        self.Pitch = 0
        self.Yaw = 0

        if self.Detect == False and self.arrival == False and self.DoD == False:
            self.Controller = 0
        elif self.Detect == True and self.arrival == False and self.DoD == False:
            self.Controller = 1
        elif self.Detect == True and self.arrival == True and self.DoD == False:
            self.Controller = 2
        elif self.Detect == True and self.arrival == True and self.DoD == True:
            self.Controller = 3
            vertex.str_mode = "Fixed  , Mode 3"
        else:
            print("Wrong Sequnce")
            self.Controller, self.Detect, self.arrival, self.DoD = 0, False, False, False


###########################################

rospy.init_node('ir_camera', anonymous=True)

rate = rospy.Rate(15)

pub = rospy.Publisher('System_Controller', Float64MultiArray, queue_size=10)

vertex = Vertex(pub)

###########################################


if __name__ == "__main__":

    try:

        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 15)

        fps = cap.get(cv2.CAP_PROP_FPS)
        print('fps', fps)

        while not rospy.is_shutdown():

            ret, frame = cap.read()

            vertex.Processing(frame)
            
            key = cv2.waitKey(1)
            if  key == 27:
                cap.release()
                vertex.out.release()
                cv2.destroyAllWindows()
                print("---end---")
                exit()

            elif key == ord('d') or key == ord('D'):
                rospy.loginfo("Found Cube, Decide its Direction")
                vertex.str_mode = "Cube Detected, Mode 1"
                vertex.Detect = True
                vertex.arrival = False
                vertex.DoD = False

            elif key == ord('a') or key == ord('A'):

                if vertex.ret_pre == 5:
                    rospy.loginfo("Arrived")
                    vertex.str_mode = "Arrived, Mode 2"
                    vertex.Detect = True
                    vertex.arrival = True
                    vertex.DoD = False
                else:
                    rospy.loginfo("Try Again")
                                
            elif key == ord('f') or key == ord('F'):
                rospy.loginfo("Decision of Direction complete, Fixed, Mode 3")
                vertex.str_mode = "Fixed  , Mode 3"
                vertex.Detect = True
                vertex.arrival = True
                vertex.DoD = True
                
            elif key == ord("q") or key == ord('Q'):
                rospy.loginfo("Initialize")
                vertex.str_mode = "Search , Mode 0"
                vertex.Detect = False
                vertex.arrival = False
                vertex.DoD = False

            rate.sleep()
                
    ##########################################
    except Exception as e :
        print(e)
    finally :
        print('end')
    