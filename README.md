# PingCheck 

The purpose of this utility is to test ICMP reachability, alert if its down and
run a config change. On recovery run another list of config changes.

# Author
Jeremy Georges - Arista Networks   - jgeorges@arista.com

# Description
PingCheck Utility


The purpose of this utility is to test ICMP reachability, alert if its down and
run a configuration change. On recovery run another list of config changes specified in a recovery file.

The failure is a logical 'OR'. Therefore, if you have two IP Hosts listed, both of them need to fail in order
for a config change to be triggered. Same situation if you have 3 IP hosts listed, in that all 3 will have to fail
in order for a configuration change to be triggered.

This extension is similar to TCPCheck found here: https://github.com/arista-eosext/TCPCheck but PingCheck uses ICMP
instead of HTTP.

To use PingCheck, after installing, add the following configuration snippets to change the default behavior.  


```
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
```

```
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
```

The CONF_FAIL and CONF_RECOVER files are just a list of commands to run at failure or at recovery. These commands
must be full commands just as if you were configuration the switch from the CLI.

For example the above referenced /mnt/flash/failed.conf file could include the following commands, which would
shutdown the BGP neighbor on failure:

```
router bgp 65001.65500
neighbor 10.1.1.1 shutdown
```

The recover.conf file would do the opposite and remove the shutdown statement:

```
router bgp 65001.65500
no neighbor 10.1.1.1 shutdown
```

This is of course just an example, and your use case would determine what config changes you'd make.

Please note, this uses the EOS SDK eAPI interaction module. You do not need to specify 'enable' and 'configure' in your 
configuration files, because it automatically goes into configuration mode.

Additional dependencies and caveats:
-This requires EOS SDK.
-All new EOS releases include the SDK.
-Tested on EOS release 4.20.10 & 4.20.1
-VRFs are not supported in this release.

## Example

### Output of 'show daemon' command
```
Agent: PingCheck (running with PID 14058)
Uptime: 0:37:46 (Start time: Tue Nov 06 16:32:34 2018)
Configuration:
Option              Value                   
------------------- ----------------------- 
CHECKINTERVAL       10                      
CONF_FAIL           /mnt/flash/failed.conf  
CONF_RECOVER        /mnt/flash/recover.conf 
HOLDDOWN            2                       
IPv4                10.1.1.2,10.1.1.6       
PINGCOUNT           3                       
SOURCE              lo0                     

Status:
Data                  Value                   
--------------------- ----------------------- 
CHECKINTERVAL:        10                      
CONF_FAIL:            /mnt/flash/failed.conf  
CONF_RECOVER:         /mnt/flash/recover.conf 
HOLDDOWN:             2                       
HealthStatus:         GOOD                    
IPv4 Ping List:       10.1.1.2,10.1.1.6       
PINGCOUNT:            3                       
Status:               Administratively Up   
```

### Syslog Messages
```
Nov  6 16:31:27 edge-leaf-A Launcher: %LAUNCHER-6-PROCESS_STOP: Configuring process 'PingCheck' to stop in role 'ActiveSupervisor'
Nov  6 16:32:34 edge-leaf-A Launcher: %LAUNCHER-6-PROCESS_START: Configuring process 'PingCheck' to start in role 'ActiveSupervisor'
Nov  6 16:32:34 edge-leaf-A PingCheck-ALERT-AGENT[14058]: %AGENT-6-INITIALIZED: Agent 'PingCheck-PingCheck' initialized; pid=14058
Nov  6 16:32:34 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck Initialized
Nov  6 16:32:34 edge-leaf-A PingCheck-ALERT-AGENT[14058]: Source Interface lo0 and Src IP 172.1.1.4 will be used.
.
After Host(s) goes down...
.
Nov  6 16:32:46 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck host 10.1.1.2 is down
Nov  6 16:59:26 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck host 10.1.1.6 is down
Nov  6 17:00:00 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck Failure State. Changing configuration for failed state
Nov  6 17:00:00 edge-leaf-A PingCheck-ALERT-AGENT[14058]: Applied Configuration changes from /mnt/flash/failed.conf
.
After Recover of Host...
.
Nov  6 17:00:25 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck host 10.1.1.6 is back up
Nov  6 17:00:37 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck host 10.1.1.2 is back up
Nov  6 17:00:39 edge-leaf-A PingCheck-ALERT-AGENT[14058]: PingCheck Recovering. Changing configure for recovered state.
Nov  6 17:00:39 edge-leaf-A PingCheck-ALERT-AGENT[14058]: Applied Configuration changes from /mnt/flash/recover.conf
```



# INSTALLATION:
Because newer releases of EOS require a SysdbMountProfile, you'll need two files - PingCheck.py and PingCheck.sysdb
PingCheck.py will need to go to an appropriate location such as /mnt/flash and PingCheck.sysdb will need to be placed in 
/usr/lib/SysdbMountProfiles. The mount profile file name MUST match the python file name. In other words, if 
you place the mount profile PingCheck in /usr/lib/SysdbMountProfiles as PingCheck, then the executable filename PingCheck.py 
must be changed to PingCheck. The filename (agent name) and mount profile name must be the same.

An RPM has been included that allows you to easily just install PingCheck as an extension and it takes care of all
the file requirements. The RPM also installs the PingCheck SDK app in /usr/local/bin. This is the preferred distribution 
method for this application.

This release has been tested on EOS 4.21.1F.

License
=======
BSD-3, See LICENSE file
