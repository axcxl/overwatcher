#!/usr/bin/python3
"""
REVISION
--------
Added revisions for overwatcher. This has nothing to do with actual versions of the code and is used to track major
changes to force rechecks of old tests and maybe keeping them up to date as new modifiers and options appear. New
revisions are kinda subjective, but for example new modifiers should mean a new revision (maybe the old tests can be
simplified with these) or big changes to the code flow.

Revision history (latest on top):
    - 20191025 (REVISION NOT CHANGED) - added posibility to run commands on the local PC as part of the test. This is
    done with a new modified LOCAL. All commands after this modifier are ran on the local PC and when the command set is
    finished, it automatically reverts to running commands on the device. Revision is not changed because this does not
    impact older test, it just improves the newer ones.
    - 20181012 : Added new modifiers - NOPRWAIT and NOTSTRICT which help with reboot parts. Old tests should be updated.
      Also prompt waits block now and the timeout part is used to recover. Major changes to read and write parts.
"""
revision = 20181012

import socket
import random
import time
import datetime
import queue
import threading
import argparse
import yaml
import os
import subprocess


class Overwatcher():
    """

    TEST AUTOMATION BASED ON SERIAL CONSOLE CONTROL AND OUTPUT.

    """

    """
    -------------------------MAIN SETUP FUNCTION. Reads the test file. Can be overloaded
    """
    def setup_test(self, test):
        """
        Function used to setup all test configurations. 

        NOTE: defaults are set before this is called, so only set what you need.
        NOTE: for backwards compatibility, this should be kept
        """
        self.name = os.path.splitext(os.path.basename(test))[0] #Used for log file, get only the name
        self.full_name = os.path.abspath(test) #Also save the full file path in the logs, because you never know

        tf = open(test, "r")
        elems = list(yaml.safe_load_all(tf))[0]

        #Thanks to YAML this was easy
        self.info = dict(elems['info'])
        self.markers = dict(elems['markers'])
        self.prompts = list(elems['prompts'])
        self.triggers = dict(elems['triggers'])
        self.actions = dict(elems['actions'])

        self.config_seq = list(elems['initconfig'])
        self.test_seq = list(elems['test'])

        #What we need to worry about are the options
        for opt in elems['options']:
            setattr(self, opt, elems['options'][opt])
    """
    -------------------------TEST RESULT FUNCTIONS, called on test ending. Can be overloaded.
    """
    def mytest_timeout(self):
        """
        Trying to improve the timeout problem. Sometimes the socket fluctuates and
        overwatcher misses some output. This should be solved with a CR.
        """
        if self.counter["test_timeouts"] == 0:
            self.setResult("timeout")
        else:
            self.counter["test_timeouts"] -= 1
            self.log("GOT A TIMEOUT, giving it another try...we have", self.counter["test_timeouts"], "left")
            self.mainTimer = self.timer_startTimer(self.mainTimer)
            if self.telnetTest is False:
                #On telnet this does not help
                self.sendDeviceCmd("") #Send a CR


    def mytest_failed(self):
        self.setResult("failed")

    def mytest_ok(self):
        self.setResult("ok")

    """
    -------------------------INIT FUNCTIONS
    """
    def config_device(self):
        """
        General device configuration
        """
        self.log("\n\/ \/ \/ \/ STARTED CONFIG!\/ \/ \/ \/\n") 
        
        last_state = self.onetime_ConfigureDevice()

        self.log("\n/\ /\ /\ /\ ENDED CONFIG!/\ /\ /\ /\ \n\n") 

    def setup_test_defaults(self):
        #In case setup_test is overloaded, set these here
        #NOTE: most likely will be overwritten in setup_test
        self.name = type(self).__name__
        self.full_name = type(self).__name__

        self.timeout = 300.0 #seconds

        self.largeCommand = 50 #what command should be sent into parts

        self.strictStates = True #by default, enforce

        self.config_seq = []
        self.test_seq = []

        self.actions = {}
        self.triggers = {}

        self.markers = {}
        self.markers_cfg = {}

        self.user_inp = {}

        self.prompts = []

        #Various test information
        self.info = {}

    def setup_modifiers_defaults(self):
        self.opt_RunTriggers = True
        self.opt_IgnoreStates = False
        self.opt_RandomExec = False
        self.opt_TimeCmd = False
        self.mod_PromptWait = True
        self.mod_RunLocal = False

        self.modifiers ={  # Quick modifier set
                "IGNORE_STATES" : self.e_IgnoreStates,
                "WATCH_STATES"  : self.d_IgnoreStates,
                "TRIGGER_START" : self.e_RunTriggers,
                "TRIGGER_STOP"  : self.d_RunTriggers,
                "SLEEP_RANDOM"  : self.sleepRandom,
                "RANDOM_START"  : self.e_RandomExecution,
                "RANDOM_STOP"   : self.d_RandomExecution,
                "COUNT"         : self.countTrigger,
                "TIMECMD"       : self.timeCommand,
                "NOTSTRICT"     : self.notStrict,
                "NOPRWAIT"      : self.d_PromptWait,
                "LOCAL"         : self.e_runLocal
                }

        #What we need to run even if states are ignored and triggers disabled
        self.critical_modifiers = ["WATCH_STATES", "TRIGGER_START"]

        self.retval = {   
                            "config failed":    3,
                            "timeout" :         2,
                            "failed" :          1,
                            "ok":               0
                      }

    def __init__(self, test, server='169.168.56.254', port=23200, runAsTelnetTest=False, endr=False):
        """
        Class init. KISS 
        NOTE: keeping default for backwards compatibility...for now
        """
        #Connection stuff
        self.server = server
        self.port = port
        if endr is False:
            self.sendendr = 'noendr'
        else:
            self.sendendr = 'endr'

        #Add support for infinite running tests - this can be set in setup_test
        #NOTE: timeout still occurs!
        self.infiniteTest = False

        #Add support for running the tests over telnet
        self.telnetTest = runAsTelnetTest
        #For telnet we need to send just a '\r', adding a dict to make things easier
        if self.telnetTest is False:
            self.eol= { 'endr': "\r\n", 'noendr': "\n"}
        else:
            self.eol= { 'endr': "\r", 'noendr': "\r" }

        #Add support for random sleep amounts - this can be set in setup_test
        self.sleep_min = 30 #seconds
        self.sleep_max = 120 #seconds
        

        self.test_max_timeouts = 2 #How many timeouts can occur per test or per loop


        #Store counts for various triggers
        self.counter = {}
        self.counter["test_loop"] = 1
        self.counter["test_timeouts"] = self.test_max_timeouts

        self.queue_state = queue.Queue() 
        self.queue_result = queue.Queue()

        self.queue_serread = queue.Queue()
        self.queue_serwrite = queue.Queue()


        #Start with defaults
        self.setup_test_defaults()
        self.setup_modifiers_defaults()

        #Use one main timer for all for now - note: needs default timeout value
        self.mainTimer = self.timer_startTimer(None)

        #Load the user setup
        self.setup_test(test)

        #Open the log file and print everything
        self.file_test = open(self.name + "_testresults.log", "w", buffering=1)
        self.print_test()

        self.sleep_sockWait = 0 #Just for startup
        self.mainSocket = self.sock_create()
        self.sleep_sockWait = 30 #seconds

        #For the config phase also use the cfg only markers
        self.statewatcher_markers = dict(self.markers_cfg)
        self.statewatcher_markers.update(self.markers)

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

        #Configure the device
        self.config_device()

        #For the normal run, revert back to the normal markers
        self.statewatcher_markers = dict(self.markers)

        #See if the config failed
        res = self.getResult(block=False)
        if res is not None:
            self.cleanAll()
            exit(res)

        #Start the TEST thread
        self.run["test"] = True
        self.th["test"] = threading.Thread(target=self.thread_MyTest, daemon=True)
        self.th["test"].start()

        res = self.getResult(block=True)
        self.cleanAll()
        exit(res)


    """
    -------------------------DEVICE CONFIGURATION
    """
    def onetime_ConfigureDevice(self):
        conf_len = len(self.config_seq)
        #Quick detour
        if conf_len == 0:
            return

        conf_idx = 0
        while(conf_idx < conf_len):
            #Look for the state
            req_state = self.config_seq[conf_idx]

            #
            ##  See if we need to run some actions
            ###
            try:
                self.log("RUNNING ACTIONS:", req_state, "=", self.actions[req_state])
                for elem in self.actions[req_state]:
                    self.sendDeviceCmd(elem)
                    self.waitDevicePrompt(elem)
                conf_idx += 1
                continue
            except KeyError:
                pass

            self.log("Looking for:", self.config_seq[conf_idx]) #idx might change
            current_state = self.getDeviceState()
            if current_state == "":
                break

            # If the required state is found 
            if req_state == current_state:
                self.log("MOVED TO STATE=", req_state)
                conf_idx += 1

            #Restart timer
            self.mainTimer = self.timer_startTimer(self.mainTimer)

                
        self.mainTimer = self.timer_stopTimer(self.mainTimer)
        return current_state

    """
    -------------------------THREADS
    """
    def thread_SerialRead(self):
        """
        Receiver thread. 
        Job: parses serial out and forms things in sentences. Does not interpret the information, except the line
        endings to form lines.
        """
        while self.run["recv"] is True:
            serout = ""
            while self.run["recv"] is True:
                #Why do the timeout: the login screen displays "User:" and no endline.
                #How do you know that the device is waiting for something in this case?
                try:
                    x = self.mainSocket.recv(1)
                except socket.timeout:
                    x = self.eol[self.sendendr].encode() #same line ending
                    break
                except OSError:
                    self.log("Reopening socket")
                    self.mainSocket = self.sock_create()
                    break #restart reading

                if not x:
                    self.log("Socket closed, reopening")
                    self.mainSocket = self.sock_create()
                    break #restart reading

                try:
                    serout += x.decode('ascii')
                except UnicodeDecodeError:
                    pass

                #Doing this to make sure we match correctly everytime
                #and to take into account the \r\n situation
                if x == self.eol[self.sendendr][0].encode():
                    break

            tmp = serout.strip() #to log the device output unmodified
            if(len(tmp) != 0):
                self.log("DEV", repr(serout))
                self.queue_serread.put(tmp)

        self.sock_close(self.mainSocket)

    def thread_SerialWrite(self):
        """
        Sender thread. 
        JOB: Sends commands to the device. Breaks large commands into pieces to not have problems with missing parts.
        """
        while self.run["send"] is True:
            cmd = self.queue_serwrite.get(block=True)
            cmd = str(cmd) #in case someone writes numbers in yml
            if cmd is None:
                break
            else:
                lcmd = len(cmd)

            #Skip endline for y/n stuff
            #NOTE: also works for 0 len cmds for sending an CR
            if lcmd != 1:
                cmd += self.eol[self.sendendr]

            while True:
                try:
                    #Improve handling of large commands sent to the device
                    if lcmd > self.largeCommand:
                        lim = int((lcmd/2)-1)
                        self.mainSocket.sendall(cmd[0:lim].encode())
                        time.sleep(0.25)
                        self.mainSocket.sendall(cmd[lim:].encode())
                    else:
                        self.mainSocket.sendall(cmd.encode())
                    break #Exit loop
                except OSError:
                    #Loop until socket is back
                    self.log("Waiting for socket to send stuff")
                    time.sleep(1)
                    continue

            self.log("SENT", repr(cmd))
        

    def thread_StateWatcher(self): 
        """
        STATE WATCHER: looks for the current state of the device
        """
        while(self.run["state_watcher"] is True):
            serout = self.getDeviceOutput()
            
            #Speed things up a bit
            if serout == "":
                continue

            for marker in self.statewatcher_markers:
                match = False
                if self.statewatcher_markers[marker] not in self.prompts:
                    #If marker is not a prompt, just look for it in the output
                    if marker in serout:
                        match = True
                else:
                    #If the marker is a prompt, we need to make sure we don't also
                    #consider it when it is part of a command sent to the device. So
                    #we try to see if there is something after it.
                    try:
                        if len(serout.strip().split(marker)[1]) == 0:
                            match = True
                    except IndexError:
                        continue

                if match is True:
                    current_state = self.statewatcher_markers[marker]

                    self.log("FOUND", current_state, "state in", serout)

                    #Run the critical modifiers, if any are present for the state
                    try:
                        actions = self.triggers[current_state]
                        for opt in actions:
                            if opt in self.critical_modifiers:
                                self.modifiers[opt](current_state)
                    except KeyError:
                        pass

                    #Notify everyone of the new state
                    self.updateDeviceState(current_state)

                    #Run the triggers of the state
                    if self.opt_RunTriggers is True:
                        try:
                            for act in self.triggers[current_state]:
                                if act not in self.modifiers.keys():
                                    self.sendDeviceCmd(act)
                                elif act not in self.critical_modifiers:
                                    #Run the rest of the normal modifiers, in order
                                    self.modifiers[act](current_state)
                        except KeyError:
                            pass

    def thread_MyTest(self):
        """
        ACTUAL TEST thread. Looks for states and executes stuff.
        """
        test_len = len(self.test_seq)
        test_idx = 0

        while self.run["test"] is True:
            if test_idx == test_len:
                if self.infiniteTest is True:
                    self.counter["test_loop"] += 1
                    self.counter["test_timeouts"] = self.test_max_timeouts #Reset the timeouts possible
                    self.log("GOT TO LOOP.....", self.counter["test_loop"])
                    test_idx = 0
                else:
                    break

            required_state = self.test_seq[test_idx]

            #
            ##  See if we need to wait for some user input
            ###
            try:
                self.log("\n\n\n", self.user_inp[required_state], "\n\n\n")
                #NOTE: stop timer while waiting for user input
                self.mainTimer = self.timer_stopTimer(self.mainTimer)

                input("EXECUTE ACTION AND PRESS ENTER")
                print("\nCONTINUING\n")
                test_idx += 1

                #Restart timer
                self.mainTimer = self.timer_startTimer(self.mainTimer)
                continue
            except KeyError:
                pass

            #
            ##  See if we need to run some actions
            ###
            try:
                #Handle RANDOM actions
                if self.tossCoin() is True:
                    self.log("RUNNING ACTIONS:", required_state, "=", self.actions[required_state])
                    for elem in self.actions[required_state]:
                        #Run any modifiers in actions
                        try:
                            self.modifiers[elem](required_state)
                            continue
                        except KeyError:
                            pass
                        if self.mod_RunLocal is False:
                            self.sendDeviceCmd(elem)
                            self.waitDevicePrompt(elem)
                        else:
                            self.runLocalCommand(elem)
                    test_idx += 1

                    # Revert back to defaults
                    self.e_PromptWait(required_state)
                    self.d_runLocal(required_state)
                continue
            except KeyError:
                pass

            #
            ##  See if we have any modifiers
            ###
            try:
                self.log("FOUND MODIFIER:", self.modifiers[required_state], "in state", required_state)

                #Needed for sleep option
                self.mainTimer = self.timer_stopTimer(self.mainTimer)

                self.modifiers[required_state](required_state)
                test_idx += 1

                #Restart timer
                self.mainTimer = self.timer_startTimer(self.mainTimer)
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


            # State changed and it isn't what we expect
            else: 
                ignore = False
                try:
                    if self.triggers[current_state][0] == "NOTSTRICT":
                        ignore = True
                except KeyError:
                    pass

                if self.strictStates is False or ignore is True:
                    self.log("STATE", current_state, "unexpected, but welcomed")
                elif ignore is False:
                    self.log("FOUND=", current_state, ", BUT WAS LOOKING FOR:", required_state)
                    self.mytest_failed()

            #TIMEOUT until next state
            self.mainTimer = self.timer_startTimer(self.mainTimer)

        self.mytest_ok()

    """
    -----------------------------------------INTERNAL APIs
    """
    def e_RunTriggers(self, state):
        #Already set, no need to do it again
        if self.opt_RunTriggers is True:
            return
        self.log("ENABLING TRIGGERS")
        self.opt_RunTriggers = True
    def d_RunTriggers(self, state):
        #Already set, no need to do it again
        if self.opt_RunTriggers is False:
            return
        self.log("DISABLING TRIGGERS")
        self.opt_RunTriggers = False

    def e_IgnoreStates(self, state):
        #Already set, no need to do it again
        if self.opt_IgnoreStates is True:
            return
        self.log("IGNORING STATES")
        self.opt_IgnoreStates = True
        if self.telnetTest is True:
            #Only on telnet, close the socket now, as this is probably a reboot
            self.sock_close(self.mainSocket)

    def d_IgnoreStates (self, state):
        #Already set, no need to do it again
        if self.opt_IgnoreStates is False:
            return
        self.log("WATCHING STATES")
        self.opt_IgnoreStates = False

    def e_RandomExecution(self, state):
        self.log("RANDOM EXECUTION")
        self.opt_RandomExec = True

    def d_RandomExecution(self, state):
        self.log("STOP RANDOM EXECUTION")
        self.opt_RandomExec = False

    def e_PromptWait(self, state):
        if self.mod_PromptWait is not True:
            self.log("WAITING FOR PROMPT AGAIN!")
            self.mod_PromptWait = True

    def d_PromptWait(self, state):
        self.log("SENDING COMMANDS WITHOUT PROMPT WAIT!")
        self.mod_PromptWait = False

    def e_runLocal(self, state):
        self.log("RUNNING ON LOCAL PC!")
        self.mod_RunLocal = True

    def d_runLocal(self, state):
        self.log("RUNNING ON DEVICE")
        self.mod_RunLocal = False

    def runLocalCommand(self, command):
        res = subprocess.call(command, shell=True)
        #TODO: retain full command output
        self.log("Command" + command + " return status " + str(res))

    def countTrigger(self, state):
        try:
            self.counter[state] += 1
        except KeyError:
            self.counter[state] = 1

        self.log("COUNTING for \'" + state + "\'...got to ", self.counter[state])
        #Display all counting stats everytime:
        for elem in self.counter:
            self.log("COUNT FOR", elem, "is", self.counter[elem])

    def timeCommand(self, state):
        self.log("TIMING NEXT COMMAND")
        self.opt_TimeCmd = True

    def notStrict(self, state):
        self.log("State", state, "treated as NOT STRICT!")

    def sleepRandom(self, state):
        duration = random.randint(self.sleep_min, self.sleep_max)
        self.log("ZzzzZZzzzzzzZzzzz....(", duration, "seconds )....")
        time.sleep(duration)
        self.log("....WAKE UP!")

    def tossCoin(self):
        if self.opt_RandomExec is False:
            return True
        else:
            ret = random.choice([True, False])
            self.log("Random coin toss showed", ret)
            return ret

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

    def waitDevicePrompt(self, cmd):
        """
        Wait until we see something defined as a device prompt. All other states 
        are ignored and put back in the queue. Prompts are consumed.
        This now blocks until it sees a prompt. If the timeout is triggered we 
        try a recovery and wait again, which should also help this. If it does 
        not, something bad happened.
        """
        if self.mod_PromptWait is True:
            self.log("Waiting for prompt for elem", cmd)
        else:
            time.sleep(1)
            return

        #Here we time the command from start
        if self.opt_TimeCmd is True:
            startOfPromptWait = datetime.datetime.now()

        while self.opt_IgnoreStates is False:
            #Look just for prompts, put everything else back
            state = self.getDeviceState()
            if state in self.prompts:
                self.log("Found prompt!")
                break
            else:
                self.updateDeviceState(state)

            time.sleep(0.2)

        #Until the prompt wait is over
        if self.opt_TimeCmd is True:
            self.opt_TimeCmd = False
            endOfPromptWait = datetime.datetime.now()
            self.log("Command", repr(cmd), "took", str(endOfPromptWait - startOfPromptWait))

    def updateDeviceState(self, state):
        """
        Wrapperr over state queue.
        """
        self.queue_state.put(state)

    def getResult(self, block=True):
        """
        Wrapper over result queue. Blocks until data is available.
        """
        ret = None
        try:
            res = self.queue_result.get(block)
        except queue.Empty:
            res = None

        if res is not None:
            self.queue_result.task_done()
            self.log("GOT RESULT:", res)
            try:
                ret = self.retval[res]
                self.log("RETURNING:", ret)
            except KeyError:
                self.log("RET VALUE UNKNOWN! Update retval option!")
                ret = -98
        elif block is True:
            self.log("RESULT QUEUE FAILED! GENERIC ERROR!")
            ret = -99

        return ret
    
    def setResult(self, res):
        """
        Wrapper over result queue. Does some filtering of the final message.
        """
        try:
            self.queue_result.put_nowait(res)
        except queue.QueueFull:
            print("FAILED TO SET RESULT")
            pass

    def timer_startTimer(self, timer):
        """
        Starts or restarts a timer using the class options (timeout and mytest_timeout)
        """
        if self.timeout == 0:
            self.log("Test has no timeout!")
            return None

        try:
            if timer is not None:
                timer.cancel()
                del timer
            timer = threading.Timer(self.timeout, self.mytest_timeout)
            timer.start()
        except UnboundLocalError:
            self.log("ERROR starting timer!")
            timer = None

        return timer

    def timer_stopTimer(self, timer):
        """
        Just stops a timer
        """
        try:
            if timer is not None:
                timer.cancel()
                del timer
        except UnboundLocalError:
            self.log("ERROR stopping timer!")
            timer = None
            pass

        return None

    def sock_create(self):
        if self.telnetTest is True and self.sleep_sockWait != 0:
            #On telnet it might close before the IGNORE STATES part
            self.e_IgnoreStates(None)
            self.d_RunTriggers(None)
            time.sleep(self.sleep_sockWait) #wait a bit before restarting connection

        self.log("Opening socket")
        connected = False
        while not connected: 
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.server, self.port))
            time.sleep(2)
            if self.telnetTest is False:
                #on serial, send an endl when creating the socket
                s.sendall(self.eol[self.sendendr].encode())
            connected = s.recv(1)

        self.log("Socket online") 
        s.setblocking(0)
        s.settimeout(1) #seconds
        
        #We might have missed something on serial
        #On telnet this is important
        self.opt_IgnoreStates = False
        self.opt_RunTriggers = True
        return s

    def sock_close(self, s):
        if s is not None:
            self.log("Closing socket")
            s.close()
            s = None

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
        ## First let's check the test. This is here to also handle the case
        ## where the setup function is overwritten. Added global revision to
        ## see it better at start of file.
        global revision
        ask = False
        try:
            if self.info['overwatcher revision required'] != revision:
                print("\nOverwatcher revision mismatch! Please check the test!\n")
                ask = True
        except KeyError:
            print("\nNo revision information in test. Please add info!\n")
            ask = True

        if ask is True:
            input("\n\nTest should be checked before running!")
            input("Press CTRL-C to stop or ENTER to continue!")

        self.file_test.write(self.name + "\n\n")
        self.file_test.write(self.full_name + "\n\n")
        for elem in self.info:
            if elem == "version":
                self.file_test.write(elem + " - " + str(self.info[elem][0]) + "\n")
            else:
                self.file_test.write(elem + " - " + str(self.info[elem]) + "\n")

        self.file_test.write("\n\n")

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
        #NOTE: result watcher is not in list!
        for thread in self.th:
            print("Joining with", thread)
            self.th[thread].join()
            print("Joined with", thread)

        print("CLOSING FILE")
        self.file_test.close()
        print("CLOSED FILE")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ultra-light test framework")

    parser.add_argument('test', help='YAML test file to run')
    parser.add_argument('--server', help='IP to telnet to',
            default='localhost')
    parser.add_argument('--port', help='Port to telnet to',
            type=int, default=3000)
    parser.add_argument('--telnet', help='Run test over telnet to device',
            action='store_true')
    parser.add_argument('--endr', help='Send a \r\n instead of just \n',
            action='store_true')

    args = parser.parse_args()

    test = Overwatcher(args.test, server=args.server, port=args.port, runAsTelnetTest=args.telnet, endr=args.endr)


