#!/bin/bash

# Script that runs whenever the container is entered
# (1) Load the bashrc since we're running run.sh with --rcfile
# (2) Setup OBIT environment variables
# (3) Starts a vncserver
# (4) Configure AIPS disks with the Obit setup
# (5) Execute AIPS login

source $HOME/.bashrc
USER=root vncserver -nolisten tcp -localhost -geometry 1440x900
cfg_aips_disks.py
echo 'Standard OBIT task configuration files are available in /obitconf'
echo 'Run aips headless with \"aips da=all notv tvok tpok\"'
