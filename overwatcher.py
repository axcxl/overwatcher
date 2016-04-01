import socket
import time
import datetime
import queue
import threading

class Overwatcher():
    """

    TEST AUTOMATION BASED ON SERIAL CONSOLE CONTROL AND OUTPUT.

    """

    """
    -------------------------FUNCTIONS THAT NEED TO BE OVERLOADED
    """
    def setup_config(self):
        """
        Function used to setup the device before the test.

        Can be used to clean-up the setup test if the config is more complicated.
        Otherwise, everything can be set in the setup_test function
        """
        return

    def setup_test(self):
        """
        Function used to setup all test configurations. 

        NOTE: defaults are set before this is called, so only set what you need.
        """
        raise NotImplementedError("PLEASE IMPLEMENT THIS IN CHILD CLASSES!")

    def setup_options(self):
        """
        Used to set the various self.opt_*** flags and the self.options callbacks.
        """
        return

    """
    -------------------------TEST RESULT FUNCTIONS, called on test ending. Can be overloaded.
    """
    def mytest_timeout(self):
        self.setResult("TIMEOUT")

    def mytest_failed(self):
        self.setResult("SEQ FAILED!")

    def mytest_ok(self):
        self.setResult("OK")

    """
    -------------------------INIT FUNCTIONS
    """
    def config_device(self):
        """
        General device configuration
        """
        self.log("\n\/ \/ \/ \/ STARTED CONFIG!\/ \/ \/ \/\n") 

        last_state = self.onetime_ConfigureDevice()
        
        #Make sure the last state is passed to the test
        self.updateDeviceState(last_state)

        self.log("\n/\ /\ /\ /\ ENDED CONFIG!/\ /\ /\ /\ \n\n") 

    def setup_test_defaults(self):
        self.name = type(self).__name__
        self.timeout = 300 #seconds

        self.config_seq = []
        self.test_seq = []

        self.actions = {}
        self.triggers = {}

        self.markers = {}
        self.markers_cfg = {}

        self.user_inp = {}

    def setup_option_defaults(self):
        self.opt_RunTriggers = True
        self.opt_IgnoreStates = False

        self.options ={  # Quick option set
                "IGNORE_STATES" : self.e_IgnoreStates,
                "WATCH_STATES"  : self.d_IgnoreStates,
                "TRIGGER_START" : self.e_RunTriggers,
                "TRIGGER_STOP"  : self.d_RunTriggers
                }

    def __init__(self, server='169.168.56.254', port=23200):

        """
        Class init. KISS 
        """
        self.server = server
        self.port = port

        self.queue_state = queue.Queue() 
        self.queue_result = queue.Queue()

        self.queue_serread = queue.Queue()
        self.queue_serwrite = queue.Queue()

        #Start with defaults
        self.setup_test_defaults()
        self.setup_option_defaults()

        #Load the user setup
        self.setup_test()
        self.setup_config()

        #Open the log file and print everything
        self.file_test = open(self.name + "_testresults.log", "w", buffering=1)
        self.print_test()

        #Prepare the threads
        self.run = {}
        self.th = {}

        self.run["recv"] = True #receiver loop - used to get out of large commands
        self.th["recv"] = threading.Thread(target=self.thread_SerialRead, daemon=True)
        self.th["recv"].start()

        self.run["send"] = True #receiver loop - used to get out of large commands
        self.th["send"] = threading.Thread(target=self.thread_SerialWrite, daemon=True)
        self.th["send"].start()

        self.run["state_watcher"] = True
        self.th["state_watcher"] = threading.Thread(target=self.thread_StateWatcher, daemon=True)
        self.th["state_watcher"].start()

        #For the config phase also use the cfg only markers
        self.statewather_markers = dict(self.markers_cfg)
        self.statewather_markers.update(self.markers)

        #Configure the device
        self.config_device()

        #Set any user options
        self.setup_options()

        #For the normal run, revert back to the normal markers
        self.statewather_markers = dict(self.markers)

        #Start the TEST thread
        self.run["test"] = True
        self.th["test"] = threading.Thread(target=self.thread_MyTest, daemon=True)
        self.th["test"].start()

        #Block until we get a result 
        result = self.getResult() 
        self.log("\n\nGOT RESULT=", result)

        #Clean and exit
        self.cleanAll()
        if "FAILED" not in result:
            print("TEST ok :)")
        else:
            print(result)

        return
    """
    -------------------------DEVICE CONFIGURATION
    """
    def onetime_ConfigureDevice(self):
        conf_len = len(self.config_seq)
        #Quick detour
        if conf_len == 0:
            return

        conf_idx = 0
        conf_timer = None

        while(conf_idx < conf_len):
            #Start a timer
            if conf_timer is not None:
                conf_timer.cancel()
            conf_timer = threading.Timer(self.timeout, self.mytest_timeout)
            conf_timer.start()

            #Look for the state
            req_state = self.config_seq[conf_idx]

            #
            ##  See if we need to run some actions
            ###
            try:
                self.log("RUNNING ACTIONS:", req_state, "=", self.actions[req_state])
                for elem in self.actions[req_state]:
                    self.sendDeviceCmd(elem)
                conf_idx += 1
                continue
            except KeyError:
                pass

            self.log("Looking for:", self.test_seq[conf_idx]) #idx might change
            current_state = self.getDeviceState()

            # If the required state is found 
            if req_state == current_state:
                self.log("MOVED TO STATE=", req_state)
                conf_idx += 1

        return current_state

    """
    -------------------------THREADS
    """
    def thread_SerialRead(self):
        """
        Receiver thread. Parses serial out and forms things in sentences.

        TODO: re-write this. Very old code and it can be done way better 
        """
        ser_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ser_sock.connect((self.server, self.port))
        ser_sock.setblocking(0)
        ser_sock.settimeout(2) #seconds
 
        while self.run["recv"] is True:
            try:
                x = ser_sock.recv(1)
            except socket.timeout:
                x = b'\n'
            serout = ""
            while((x != b'\n') and (x != b'\r') and (self.run["recv"] is True)):
                if(x != b'\n') and (x != b'\r'):
                    try:
                        serout += x.decode('ascii')
                        if(x == b'>') or (x == b'#') or (x == b'\b'):
                            break
                    except UnicodeDecodeError:
                        pass
                #Why do the timeout: the login screen displays "User:" and no endline.
                #How do you know that the device is waiting for something in this case?
                try:
                    x = ser_sock.recv(1)
                except socket.timeout:
                    x = b'\n'

            serout = serout.strip()
            self.queue_serread.put(serout)
            self.logNoPrint(serout)

        ser_sock.close()

    def thread_SerialWrite(self):
        """
        Sender thread. Sends commands to the device.
        """
        ser_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ser_sock.connect((self.server, self.port))
 
        while self.run["send"] is True:
            cmd = self.queue_serwrite.get(block=True)
            if cmd is None:
                break

            ser_sock.sendall(cmd.encode())
            self.log("SENT", cmd)
            time.sleep(0.25)
        

    def thread_StateWatcher(self): 
        """
        STATE WATCHER: looks for the current state of the device
        """
        while(self.run["state_watcher"] is True):
            serout = self.getDeviceOutput()

            
            #Speed things up a bit
            if serout == "":
                continue

            for marker in self.statewather_markers:
                if marker in serout:
                    current_state = self.statewather_markers[marker]

                    self.log("FOUND", current_state, "state in", serout)

                    #First run all the options for the state
                    try:
                        actions = self.triggers[current_state]
                        for opt in actions:
                            self.options[opt]()
                    except KeyError:
                        pass

                    #Notify everyone of the new state
                    self.updateDeviceState(current_state)

                    #Run the other triggers
                    if self.opt_RunTriggers is True:
                        try:
                            actions = self.triggers[current_state]
                            for act in actions:
                                if act not in self.options.keys():
                                    self.sendDeviceCmd(act)
                        except KeyError:
                            pass

    def thread_MyTest(self):
        """
        ACTUAL TEST thread. Looks for states and executes stuff.
        """
        test_len = len(self.test_seq)
        test_idx = 0
        wait_for_state = None

        while((test_idx < test_len) and (self.run["test"] is True)):
            required_state = self.test_seq[test_idx]

            #
            ##  See if we need to wait for some user input
            ###
            try:
                self.log("\n\n\n", self.user_inp[required_state], "\n\n\n")
                #NOTE: stop timer while waiting for user input
                try:
                    if wait_for_state is not None:
                        wait_for_state.cancel()
                        del wait_for_state
                except UnboundLocalError:
                    wait_for_state = None
                    pass

                input("EXECUTE ACTION AND PRESS ENTER")
                print("\nCONTINUING\n")
                test_idx += 1

                #Restart timer
                wait_for_state = threading.Timer(self.timeout, self.mytest_timeout)
                wait_for_state.start()
                continue
            except KeyError:
                pass

            #
            ##  See if we need to run some actions
            ###
            try:
                self.log("RUNNING ACTIONS:", required_state, "=", self.actions[required_state])
                for elem in self.actions[required_state]:
                    self.sendDeviceCmd(elem)
                test_idx += 1
                continue
            except KeyError:
                pass

            #
            ##  See if we need to set any options
            ###
            try:
                self.options[required_state]()
                test_idx += 1
                continue
            except KeyError:
                pass

            self.log("Looking for:", self.test_seq[test_idx]) #idx might change
            current_state = self.getDeviceState()

            if self.opt_IgnoreStates is True:
                self.log("IGNORED STATE", current_state)
                continue

            # If the required state is found 
            if required_state == current_state:
                self.log("MOVED TO STATE=", required_state)
                test_idx += 1

                #TIMEOUT until next state
                try:
                    if wait_for_state is not None:
                        wait_for_state.cancel()
                        del wait_for_state
                    wait_for_state = threading.Timer(self.timeout, self.mytest_timeout)
                    wait_for_state.start()
                except UnboundLocalError:
                    wait_for_state = None
            # State changed and it isn't what we expect
            else: 
                self.log("FOUND=", current_state, ", BUT WAS LOOKING FOR:", required_state)
                self.mytest_failed()

        self.mytest_ok()

    """
    -----------------------------------------INTERNAL APIs
    """
    def e_RunTriggers(self):
        self.log("ENABLING TRIGGERS")
        self.opt_RunTriggers = True
    def d_RunTriggers(self):
        self.log("DISABLING TRIGGERS")
        self.opt_RunTriggers = False

    def e_IgnoreStates(self):
        self.log("IGNORING STATES")
        self.opt_IgnoreStates = True
    def d_IgnoreStates (self):
        self.log("WATCHING STATES")
        self.opt_IgnoreStates = False

    def getDeviceOutput(self):
        """
        Wrapper over serial receive queue. Blocks until data is available.

        Returns "" if queue is closing.
        """
        serout = self.queue_serread.get(block=True)
        self.queue_serread.task_done()
        if serout is None:
            return ""
        else:
            return serout

    def sendDeviceCmd(self, cmd):
        """
        Wrapper over serial send queue.
        """
        self.queue_serwrite.put(cmd)


    def getDeviceState(self):
        """
        Wrapper over state queue. Blocks until data is available.

        Returns "" if queue is closing.
        """
        state = self.queue_state.get(block=True)
        self.queue_state.task_done()
        if state is None:
            return ""
        else:
            return state

    def waitDeviceState(self, state):
        while(self.getDeviceState() != state):
            time.sleep(0.1)

    def updateDeviceState(self, state):
        """
        Wrapperr over state queue.
        """
        self.queue_state.put(state)

    def getResult(self):
        """
        Wrapper over result queue. Blocks until data is available.
        """
        res = self.queue_result.get(block=True)
        self.queue_result.task_done()
        if res is None:
            return "FAILED"
        else:
            return res
    
    def setResult(self, res):
        """
        Wrapper over result queue. Does some filtering of the final message.
        """
        try:
            if res == "OK":
                self.queue_result.put_nowait("OK")
            else:
                self.queue_result.put_nowait("FAILED" + str(res))
        except queue.QueueFull:
            print("FAILED TO SET RESULT")
            pass

    def logNoPrint(self, *args):
        outtext = ""
        for elem in args:
            outtext += str(elem)
            outtext += " "

        try:
            self.file_test.write(str(datetime.datetime.now()) + ' - ' + outtext + "\n")
            return outtext
        except ValueError:
            return ""

    def log(self, *args):
        print(str(datetime.datetime.now()), self.logNoPrint("+++>", *args))

    def print_test(self):
        self.file_test.write(self.name + "\n\n")

        self.file_test.write("MARKERS:\n")
        self.file_test.write(str(self.markers) + "\n")
        self.file_test.write("MARKERS CFG:\n")
        self.file_test.write(str(self.markers_cfg) + "\n")
        self.file_test.write("TRIGGERS:\n")
        self.file_test.write(str(self.triggers) + "\n")
        self.file_test.write("CONF SEQ:\n")
        self.file_test.write(str(self.config_seq) + "\n")
        self.file_test.write("TEST SEQ:\n")
        self.file_test.write(str(self.test_seq) + "\n")
        self.file_test.write("USER_INP\n")
        self.file_test.write(str(self.user_inp) + "\n")
        self.file_test.write("ACTIONS:\n")
        self.file_test.write(str(self.actions) + "\n")

        self.file_test.write("RUN TRIGGERS=" + str(self.opt_RunTriggers) + "\n")
        self.file_test.write("IGNORE STATES=" + str(self.opt_IgnoreStates) + "\n")

        self.file_test.write("\n\nTEST START:\n\n")

    def cleanAll(self):
        print(self.run)
        for elem in self.run:
            self.run[elem] = False
            print("Ended", elem)

        self.queue_state.put(None)
        self.queue_serread.put(None)
        self.queue_serwrite.put(None)

        print(self.th)
        for thread in self.th:
            print("Joining with", thread)
            self.th[thread].join()
            print("Joined with", thread)

        print("CLOSING FILE")
        self.file_test.close()
        print("CLOSED FILE")
