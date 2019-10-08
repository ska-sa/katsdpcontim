#!groovy

@Library('katsdpjenkins') _
katsdp.killOldJobs()

katsdp.setDependencies([
    'ska-sa/katsdpdockerbase/master',
    'ska-sa/katpoint/master',
    'ska-sa/katdal/master',
    'ska-sa/katsdpservices/master',
    'ska-sa/katsdptelstate/master'])
katsdp.standardBuild(subdir: 'katsdpcontim',
                     cuda: true,
                     label: 'cpu-avx2',
                     docker_timeout: [time: 90, unit: 'MINUTES'])
katsdp.mail('sdpdev+katsdpcontim@ska.ac.za')
