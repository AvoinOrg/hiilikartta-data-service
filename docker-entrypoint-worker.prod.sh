#!/bin/bash
source /root/.bashrc >/dev/null 2>&1

poetry run saq --workers 10 app.saq_worker.settings --web