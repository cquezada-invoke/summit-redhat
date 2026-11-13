#!/bin/bash
cut -c1-200 ~/faceops-autodeploy/estado; tail -5 ~/faceops-autodeploy/autodeploy.log | cut -c1-200; cd ~/summit-redhat; git status -sb | head -1
