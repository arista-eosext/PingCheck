#!/usr/bin/env python
# Copyright (c) 2018 Arista Networks, Inc.  All rights reserved.
# Arista Networks, Inc. Confidential and Proprietary.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#  - Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#  - Redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution.
#  - Neither the name of Arista Networks nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL ARISTA NETWORKS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR
# BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN
# IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
'''
PingCheck Utility

The purpose of this utility is to test ICMP ping reachability, alert if its down and
run a config change. On recovery run another list of config changes.

Add the following configuration snippets to change the default behavior.
Current version supports a list of hosts. The host checking is a logical OR, so
only one host needs to respond. This is designed to reduce false positives.

daemon PingCheck
   exec /usr/local/bin/PingCheck
   option CHECKINTERVAL value 5
   option CONF_FAIL value /mnt/flash/failed.conf
   option CONF_RECOVER value /mnt/flash/recover.conf
   option PINGCOUNT value 2
   option PINGTIMEOUT value 2
   option HOLDDOWN value 1
   option HOLDUP value 1
   option IPv4 value 10.1.1.1,10.1.2.1
   option SOURCE value et1
   no shutdown


Config Option explanation:
    - CHECKINTERVAL is the time in seconds to check hosts. Default is 5 seconds.
    - IPv4 is the address(s) to check. Mandatory parameter. Multiple addresses are comma separated
    - CONF_FAIL is the config file to apply the snippets of config changes. Mandatory parameter.
    - CONF_RECOVER is the config file to apply the snippets of config changes
      after recovery of Neighbor. Mandatory parameter.
    - PINGCOUNT is the number of ICMP Ping Request messages to send. Default is 2.
    - HOLDDOWN is the number of iterations to wait before declaring all hosts up. Default is 1
      which means take immediate action.
    - HOLDUP is the number of iterations to wait before declaring all hosts down. Default is 1
    - SOURCE is the source interface (as instantiated to the kernel) to generate the pings fromself.
      This is optional. Default is to use RIB/FIB route to determine which interface to use as sourceself.
    - PINGTIMEOUT is the ICMP ping timeout in seconds. Default value is 2 seconds.


The CONF_FAIL and CONF_RECOVER files are just a list of commands to run at either Failure or at recovery. These commands
must be full commands just as if you were configuration the switch from the CLI.

For example the above referenced /mnt/flash/failed.conf file could include the following commands, which would
shutdown the BGP neighbor on failure:
router bgp 65001.65500
neighbor 10.1.1.1 shutdown

The recover.conf file would do the opposite and remove the shutdown statement:
router bgp 65001.65500
no neighbor 10.1.1.1 shutdown

This is of course just an example, and your use case would determine what config changes you'd make.

Please note, because this extension uses the EOS SDK eAPI interation module, you do not need to have 'enable and 'configure'
in your config change files. This is because, the EOS SDK eAPI interation module is already in configuration mode.
'''
#************************************************************************************
# Change log
# ----------
# Version 1.0.0  - 11/2/2018 - Jeremy Georges -- jgeorges@arista.com --  Initial Version
# Version 1.2.0  - 11/9/2018 - J. Georges - Changed Source IP lookup to eAPI module. Using the socket call
#                                           when the interface was shut down caused a core dump.
#                                           Pinging now executed through eAPI module also. This removes the need
#                                           for subprocess module.
#                                           Added ping timeout option.
#
#*************************************************************************************
#
#
#****************************
#GLOBAL VARIABLES -         *
#****************************
# These are the defaults. The config can override these
#Default check Interval in seconds
CHECKINTERVAL = 5
#
#CURRENTSTATUS   1 is Good, 0 is Down
CURRENTSTATUS = 1

#Number of ICMP pings to send to each host.
PINGCOUNT = 2

#Ping timeout
PINGTIMEOUT = 2

#Default number of failures before declaring a neighbor(s) up. 1 means we react immediately
HOLDDOWN = 1
#
#Default number of failures before declaring a neighbor(s) down. 1 means we react immediately
HOLDUP = 1


#We need a global list that will be there between iterations. Including after a reconfiguration
DEADIPV4=[]
GOODIPV4=[]

#Global counter that we'll use between iterations
ITERATION = 0

#****************************
#*     MODULES              *
#****************************
#
import sys
import syslog
import eossdk
import os.path
import os
import simplejson
import re


#***************************
#*     CLASSES             *
#***************************
class PingCheckAgent(eossdk.AgentHandler,eossdk.TimeoutHandler):
    def __init__(self, sdk, timeoutMgr,EapiMgr):
        self.agentMgr = sdk.get_agent_mgr()
        self.tracer = eossdk.Tracer("PingCheckPythonAgent")
        eossdk.AgentHandler.__init__(self, self.agentMgr)
        #Setup timeout handler
        eossdk.TimeoutHandler.__init__(self, timeoutMgr)
        self.tracer.trace0("Python agent constructed")
        self.EapiMgr = EapiMgr


    def on_initialized(self):
        self.tracer.trace0("Initialized")
        syslog.syslog("PingCheck Initialized")
        self.agentMgr.status_set("Status:", "Administratively Up")

        #We'll pass this on to on_agent_option to process each of these.
        self.on_agent_option("CONF_FAIL", self.agentMgr.agent_option("CONF_FAIL"))
        self.on_agent_option("CONF_RECOVER", self.agentMgr.agent_option("CONF_RECOVER"))
        IPv4 = self.agentMgr.agent_option("IPv4")
        if not IPv4:
           # No IPv4 list of IPs initially set
           self.agentMgr.status_set("IPv4 Ping List:", "None")
        else:
           # Handle the initial state
           self.on_agent_option("IPv4", IPv4)

        #Lets check the extra parameters and see if we should override the defaults
        #This is mostly for the status message.
        global CHECKINTERVAL
        if self.agentMgr.agent_option("CHECKINTERVAL"):
            self.on_agent_option("CHECKINTERVAL", self.agentMgr.agent_option("CHECKINTERVAL"))
        else:
            #We'll just use the default time specified by global variable
            self.agentMgr.status_set("CHECKINTERVAL:", "%s" % CHECKINTERVAL)

        global PINGCOUNT
        if self.agentMgr.agent_option("PINGCOUNT"):
            self.on_agent_option("PINGCOUNT", self.agentMgr.agent_option("PINGCOUNT"))
        else:
            #We'll just use the default pingcount specified by global variable
            self.agentMgr.status_set("PINGCOUNT:", "%s" % PINGCOUNT)

        global HOLDDOWN
        if self.agentMgr.agent_option("HOLDDOWN"):
            self.on_agent_option("HOLDDOWN", self.agentMgr.agent_option("HOLDDOWN"))
        else:
            #We'll just use the default holddown specified by global variable
            self.agentMgr.status_set("HOLDDOWN:", "%s" % HOLDDOWN)

        global HOLDUP
        if self.agentMgr.agent_option("HOLDUP"):
            self.on_agent_option("HOLDUP", self.agentMgr.agent_option("HOLDUP"))
        else:
            #We'll just use the default holdup specified by global variable
            self.agentMgr.status_set("HOLDUP:", "%s" % HOLDUP)

        global PINGTIMEOUT
        if self.agentMgr.agent_option("PINGTIMEOUT"):
            self.on_agent_option("PINGTIMEOUT", self.agentMgr.agent_option("PINGTIMEOUT"))
        else:
            #We'll just use the default holddown specified by global variable
            self.agentMgr.status_set("PINGTIMEOUT:", "%s" % PINGTIMEOUT)

        #Some basic mandatory variable checks. We'll check this when we have a
        #no shut on the daemon. Add some notes in comment and Readme.md to recommend
        #a shut and no shut every time you make parameter changes...

        self.agentMgr.status_set("Health Status:", "Unknown")


        #Start our handler now.
        self.timeout_time_is(eossdk.now())


    def on_agent_option(self, optionName, value):
        #options are a key/value pair
        #Here we set the status output when user does a show agent command
        if optionName == "IPv4":
            if not value:
                self.tracer.trace3("IPv4 List Deleted")
                self.agentMgr.status_set("IPv4 Ping List:", "None")
            else:
                self.tracer.trace3("Adding IPv4 Address list to %s" % value)
                self.agentMgr.status_set("IPv4 Ping List:", "%s" % value)

        if optionName == "CONF_FAIL":
            if not value:
                self.tracer.trace3("CONF_FAIL Deleted")
                self.agentMgr.status_set("CONF_FAIL:", "None")
            else:
                self.tracer.trace3("Adding CONF_FAIL %s" % value)
                self.agentMgr.status_set("CONF_FAIL:", "%s" % value)
        if optionName == "CONF_RECOVER":
            if not value:
                self.tracer.trace3("CONF_RECOVER Deleted")
                self.agentMgr.status_set("CONF_RECOVER:", "None")
            else:
                self.tracer.trace3("Adding CONF_RECOVER %s" % value)
                self.agentMgr.status_set("CONF_RECOVER:", "%s" % value)
        if optionName == "HOLDDOWN":
            if not value:
                self.tracer.trace3("HOLDDOWN Deleted")
                self.agentMgr.status_set("HOLDDOWN:", HOLDDOWN)
            else:
                self.tracer.trace3("Adding HOLDDOWN %s" % value)
                self.agentMgr.status_set("HOLDDOWN:", "%s" % value)
        if optionName == "HOLDUP":
            if not value:
                self.tracer.trace3("HOLDUP Deleted")
                self.agentMgr.status_set("HOLDUP:", HOLDUP)
            else:
                self.tracer.trace3("Adding HOLDUP %s" % value)
                self.agentMgr.status_set("HOLDUP:", "%s" % value)
        if optionName == "PINGCOUNT":
            if not value:
                self.tracer.trace3("PINGCOUNT Deleted")
                self.agentMgr.status_set("PINGCOUNT:", PINGCOUNT)
            else:
                self.tracer.trace3("Adding PINGCOUNT %s" % value)
                self.agentMgr.status_set("PINGCOUNT:", "%s" % value)
        if optionName == "PINGTIMEOUT":
            if not value:
                self.tracer.trace3("PINGTIMEOUT Deleted")
                self.agentMgr.status_set("PINGTIMEOUT:", PINGCOUNT)
            else:
                self.tracer.trace3("Adding PINGTIMEOUT %s" % value)
                self.agentMgr.status_set("PINGTIMEOUT:", "%s" % value)
        if optionName == "CHECKINTERVAL":
            if not value:
                self.tracer.trace3("CHECKINTERVAL Deleted")
                self.agentMgr.status_set("CHECKINTERVAL:", CHECKINTERVAL)
            else:
                self.tracer.trace3("Adding CHECKINTERVAL %s" % value)
                self.agentMgr.status_set("CHECKINTERVAL:", "%s" % value)

    def on_agent_enabled(self, enabled):
        #When shutdown set status and then shutdown
        if not enabled:
            self.tracer.trace0("Shutting down")
            self.agentMgr.status_del("Status:")
            self.agentMgr.status_set("Status:", "Administratively Down")
            self.agentMgr.agent_shutdown_complete_is(True)


    def check_vars(self):
        '''
        Do some basic config checking. Return 1 if all is good. Else return
        0 if config is missing a key parameter and send a syslog message so user
        knows what is wrong.
        Very basic existance testing here. Maybe add later some syntax testing...
        '''

        #Check IP LIST.
        #TODO, parse the IPv4 list and make sure there are no typos.
        if not self.agentMgr.agent_option("IPv4"):
            syslog.syslog("IPv4 parameter is not set. This is a mandatory parameter")
            return 0

        #Make sure CONF file mandatory parameters are set
        if not self.agentMgr.agent_option("CONF_FAIL"):
            syslog.syslog("CONF_FAIL parameter is not set. This is a mandatory parameter")
            return 0
        if not self.agentMgr.agent_option("CONF_RECOVER"):
            syslog.syslog("CONF_RECOVER parameter is not set. This is a mandatory parameter")
            return 0

        #If we get here, then we know our config file parameters have been setself.
        #Now lets check to make sure the files actually exist.
        TESTFILE=self.agentMgr.agent_option("CONF_FAIL")
        if not os.path.isfile(TESTFILE):
            syslog.syslog("CONF_FAIL %s does not exist. This is mandatory." % TESTFILE)
            return 0
        if os.path.getsize(TESTFILE) == 0:
            syslog.syslog("CONF_FAIL %s is blank. You need at least one command listed." % TESTFILE)
            return 0
        TESTFILE=self.agentMgr.agent_option("CONF_RECOVER")
        if not os.path.isfile(TESTFILE):
            syslog.syslog("CONF_RECOVER %s does not exist. This is mandatory." % TESTFILE)
            return 0
        if os.path.getsize(TESTFILE) == 0:
            syslog.syslog("CONF_RECOVER %s is blank. You need at least one command listed." % TESTFILE)
            return 0

        #Check pingtimeout settings if it was set. Can only be 0-3600
        if self.agentMgr.agent_option("PINGTIMEOUT"):
            if self.agentMgr.agent_option("PINGTIMEOUT") > 3600:
                syslog.syslog("PINGTIMEOUT must not exceed 3600 seconds.")

        #Check the Source variable if it is defined..
        if self.agentMgr.agent_option("SOURCE"):
            #check using eAPI module.
            if self.check_interface(self.agentMgr.agent_option("SOURCE")) == False:
                syslog.syslog("Source Interface %s is not valid. " % self.agentMgr.agent_option("SOURCE"))
                return 0
        #If we get here, then we're good!
        return 1

    def on_timeout(self):
        '''
         This is the function/method where we do the exciting stuff :-)
        '''
        #Global variables are needed
        global CHECKINTERVAL
        global CURRENTSTATUS
        global PINGCOUNT
        global ITERATION


        # Just in case someone changes the options while daemon is running
        # we should go ahead and check our parameters on each iteration.
        # if its not a 1, then we fail check and will just wait till next iteration
        # and will show this via the status.
        if self.check_vars() == 1:

            #Here we do all the fun work and testing
            IPv4 = self.agentMgr.agent_option("IPv4")
            if self.agentMgr.agent_option("PINGCOUNT"):
                PINGS2SEND = self.agentMgr.agent_option("PINGCOUNT")
            else:
                #Else we'll use the default value of PINGCOUNT
                PINGS2SEND=PINGCOUNT

            #Check state, are we UP or FAILED state?
            #If up, lets check each of our addresses.
            #For this particular use case, its a logical OR for our addresses.
            #If any are up, then we mark this as good
            #If ALL are down, then we mark as bad
            #We also need to mark the iteration number which is important
            # for our holddown number.
            #

            #We could just react to single failure or recovery. But this is not as versatile.
            #What happens if remote rate limits pings so we have a false positive? This is why
            # we need to make sure that all our hosts in our list are down before we consider
            #this an issue.
            #Lets test each host in list and then we will populate DEAD or GOOD global list.
            #Then it is easier to do our logic or change it after all the checks.
            global DEADIPV4
            global GOODIPV4
            if IPv4:
                EachAddress = IPv4.split(',')
                for host in EachAddress:
                    if SOURCEINTFADDR:
                        pingstatus = self.pingDUTeAPI(4,str(host),PINGS2SEND,SOURCEINTFADDR)
                    else:
                        pingstatus = self.pingDUTeAPI(4,str(host),PINGS2SEND)
                    #After ping status, lets go over all the various test cases below
                    if pingstatus == True:
                        #Its alive - UP
                        #Check to see if it was in our dead list
                        if host in DEADIPV4:
                            #Notify that its back up.
                            syslog.syslog('PingCheck host %s is back up' % str(host))
                            DEADIPV4.remove(host)
                        if host not in GOODIPV4:
                        	GOODIPV4.append(host)
                    else:
                        #Its not alive  - DOWN
                        if host not in DEADIPV4:
                            syslog.syslog('PingCheck host %s is down' % str(host))
                            DEADIPV4.append(host)
                        if host in GOODIPV4:
                        	#need to remove it from our GOOD list.
                            GOODIPV4.remove(host)

            #We need to have some local variables to use for HOLDUP and HOLDDOWN because the admin
            #might change the values from the default. So lets just check this on each iteration.
            #But if the admin changes this in the middle of an interation check, we should make sure ITERATION
            # is greater than or equal to the HOLDDOWN or HOLDUP values so we don't get stuck.

            if self.agentMgr.agent_option("HOLDDOWN"):
                HOLDDOWNLOCAL = self.agentMgr.agent_option("HOLDDOWN")
            else:
                HOLDDOWNLOCAL = HOLDDOWN
            if self.agentMgr.agent_option("HOLDUP"):
                HOLDUPLOCAL = self.agentMgr.agent_option("HOLDUP")
            else:
                HOLDUPLOCAL = HOLDUP

			# Now we have all the ping state for each host. Lets do our additional logic here
            # Current implementaion is logical OR. So all we need is at least one host in GOODIPV4 list and we pass
            if len(GOODIPV4) > 0:
            	# We have some life here...now we need to determine whether to recover or not based on our HOLDDOWN.
                if CURRENTSTATUS == 0:
                	#We were down, now determine if we should recover yet.
                    if ITERATION >= int(HOLDDOWNLOCAL):
                    	# Recover
                        CURRENTSTATUS = 1
                        ITERATION = 0
                        syslog.syslog("PingCheck Recovering. Changing configure for recovered state.")
                        #RUN CONFIG Change
                        self.change_config('RECOVER')
                    else:
                    	ITERATION += 1
                        #We need to wait till we hit our HOLDDOWN counter so we dampen a flapping condition if so exists
            else:
            	#We get here when everything is down...nothing in GOODIPV4 list
                #Determine, are we already down? If so, noop. If not, then we need to determine if we are at HOLDDOWN.
                if CURRENTSTATUS == 1:
                	#Determine if we need to do something
                    if ITERATION >= int(HOLDUPLOCAL):
                    	syslog.syslog("PingCheck Failure State. Changing configuration for failed state")
                        # run config change failure
                        self.change_config('FAIL')
                        #Set Currentstatus to 0, we're now in failed state
                        CURRENTSTATUS = 0
                        #Reset ITERATION
                        ITERATION = 0
                    else:
                    	ITERATION += 1

            #Set current state via HealthStatus with agentMgr.
            if CURRENTSTATUS == 1:
                self.agentMgr.status_set("Health Status:", "GOOD")
            else:
                self.agentMgr.status_set("Health Status:", "FAIL")

        else:
            #If we failed the config check, then we land here and just skip any other processing
            #and set Health status to INACTIVE.
            #Once the config checks out, then we'll change it above with either GOOD or FAIL
            #dependent on our ping checks.
            self.agentMgr.status_set("Health Status:", "INACTIVE")

        #Wait for CHECKINTERVAL
        if self.agentMgr.agent_option("CHECKINTERVAL"):
            self.timeout_time_is(eossdk.now() + int(self.agentMgr.agent_option("CHECKINTERVAL")))
        else:
            self.timeout_time_is(eossdk.now() + int(CHECKINTERVAL))

    def check_interface(self,SOURCE):
        """
        Check the interface to see if it is a legitmate interface

        """
        #Use EapiMgr to show interfaces and we'll make sure this
        #interface is ok to use.
        #Should we worry about capitalizing first char?
        global SOURCEINTFADDR
        try:
            showint = self.EapiMgr.run_show_cmd("show ip interface %s" % SOURCE)
            interfaceID = simplejson.loads(showint.responses()[0])
            for item in interfaceID['interfaces'].keys():
                ipaddr = interfaceID['interfaces'][item]['interfaceAddress']['primaryIp']['address']
        except:
            ipaddr = ''
        if ipaddr:
            SOURCEINTFADDR = ipaddr
            return ipaddr
        else:
            return False

    def pingDUTeAPI(self,protocol,hostname, pingcount, source=None):
        """
        Ping a DUT(s).

        Pass the following to the function:
            protocol (Version 4 or 6)
            host
            pingcount (number of ICMP pings to send)

            return False if ping fails
            return True if ping succeeds
        """
        global PINGTIMEOUT
        #
        #Lets generate the entire ping command / string
        if source is None:
            pingline = "ping %s repeat %s" % (hostname,pingcount)
        else:
            pingline = "ping %s repeat %s source %s" % (hostname,pingcount,source)
        if self.agentMgr.agent_option("PINGTIMEOUT"):
            pingline += " timeout %s" % self.agentMgr.agent_option("PINGTIMEOUT")
        else:
            pingline += " timeout %s" % str(PINGTIMEOUT)
        pinghost = self.EapiMgr.run_show_cmd(pingline)
        pinghostresponse=simplejson.loads(pinghost.responses()[0])
        output = pinghostresponse.get("messages")
        received = re.findall(r"(\d+) received", output[0])

        if len(received) == 0:
            #If we get here, then interfaces are down and we can not ping anyway
            #Error message   -- connect: Network is unreachable
            #falls under this category. Pretty much any error message falls under this.
            return False
        else:
            # we can safely assume this is a list as that is what re.findall returns
            #if we get to this point because length check above was false.
            if str(received[0]) == '0':
                #we received 0 ICMP replies. Therefore host is down.
                return False
            else:
                #we got something greater than 0. That is good enough of a check
                return True


    def change_config(self, STATUS):
        '''
        Method to change configuration of switch.
        If STATUS is FAIL, then run CONF_FAIL via eAPI API
        If STATUS RECOVER (or else) then run CONF_RECOVER via eAPI API
        '''
        CONF_FAIL = self.agentMgr.agent_option("CONF_FAIL")
        CONF_RECOVER = self.agentMgr.agent_option("CONF_RECOVER")
        if STATUS == 'FAIL':
            self.tracer.trace0("Status FAIL. Applying config changes")
            with open(CONF_FAIL) as fh:
                configfile = fh.readlines()
            #Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            #Check to make sure user has not specified 'enable' as the first command. This will error  in command mode
            if configfile[0] == 'enable':
                del configfile[0]
            #Now apply config changes
            try:
                applyconfig = self.EapiMgr.run_config_cmds([z for z in configfile])
                if(applyconfig.success()):
                    syslog.syslog("Applied Configuration changes from %s" % CONF_FAIL)
                else:
                    syslog.syslog("Unable to apply configuration changes from %s" % CONF_FAIL)
            except:
                syslog.syslog("Unable to apply config via eAPI interaction module in EOS SDK.")
                return 0
        else:
            self.tracer.trace0("Status Recover. Applying config changes.")
            with open(CONF_RECOVER) as fh:
                configfile = fh.readlines()
            #Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            #Check to make sure user has not specified 'enable' as the first command. This will error  in command mode
            if configfile[0] == 'enable':
                del configfile[0]

            #Now apply config changes
            try:
                applyconfig = self.EapiMgr.run_config_cmds([z for z in configfile])
                if(applyconfig.success()):
                    syslog.syslog("Applied Configuration changes from %s" % CONF_RECOVER)
                else:
                    syslog.syslog("Unable to apply configuration changes from %s" % CONF_RECOVER)
            except:
                syslog.syslog("Unable to apply config via eAPI interaction module in EOS SDK.")
                return 0

        return 1

#=============================================
# MAIN
#=============================================
def main():
    syslog.openlog(ident="PingCheck-ALERT-AGENT",logoption=syslog.LOG_PID, facility=syslog.LOG_LOCAL0)
    sdk = eossdk.Sdk()
    PingCheck = PingCheckAgent(sdk, sdk.get_timeout_mgr(),sdk.get_eapi_mgr())
    sdk.main_loop(sys.argv)
    # Run the agent until terminated by a signal

if __name__ == "__main__":
    main()
