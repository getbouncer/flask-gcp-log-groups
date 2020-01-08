# Copyright 2018 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0
# license that can be found in the LICENSE file.

import logging
import json
import datetime
import os

import time
from flask import Flask, has_request_context
from flask import request, Response, render_template, g, jsonify, current_app

from google.cloud import logging as gcplogging
from google.cloud.logging.resource import Resource

from flask_gcp_log_groups.background_thread import BackgroundThreadTransport

_GLOBAL_RESOURCE = Resource(type='global', labels={})

logger = logging.getLogger(__name__)
client = gcplogging.Client()

class GCPHandler(logging.Handler):
    
    def __init__(self, app, parentLogName='request', childLogName='application', 
                traceHeaderName=None,labels=None, resource=None):
        logging.Handler.__init__(self)
        self.app = app
        self.labels=labels
        self.traceHeaderName = traceHeaderName
        if (resource is None):
            resource = _GLOBAL_RESOURCE
        else:
            resource = Resource(type=resource['type'], labels=resource['labels'])
        self.resource = resource
        self.transport_parent = BackgroundThreadTransport(client, parentLogName)
        self.transport_child = BackgroundThreadTransport(client, childLogName)           
        self.mLogLevels = []
        if app is not None:
            self.init_app(app)
            
    def emit(self, record):
        if not has_request_context():
            return
        msg = self.format(record)
        SEVERITY = record.levelname

        self.mLogLevels.append(record.levelno)
        TRACE = None
        SPAN = None
        if (self.traceHeaderName in request.headers.keys()):
          # trace can be formatted as "X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=TRACE_TRUE"
          rawTrace = request.headers.get(self.traceHeaderName).split('/')
          trace_id = rawTrace[0]
          TRACE = "projects/{project_id}/traces/{trace_id}".format(
              project_id=os.getenv('GOOGLE_CLOUD_PROJECT'),
              trace_id=trace_id)
          if (len(rawTrace) > 1):
              SPAN = rawTrace[1].split(';')[0]

        self.transport_child.send(
                msg,
                timestamp=datetime.datetime.utcnow(),                
                severity=SEVERITY,
                resource=self.resource,
                labels=self.labels,
                trace=TRACE,
                span_id=SPAN)            

    def init_app(self, app):

        # capture the http_request time
        @app.before_request
        def before_request():
            g.request_start_time = time.time()
            g.request_time = lambda: "%.5fs" % (time.time() - g.request_start_time)

        # always log the http_request@ default INFO
        @app.after_request
        def add_logger(response):
            TRACE = None
            SPAN = None
            if (self.traceHeaderName in request.headers.keys()):
                # trace can be formatted as "X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=TRACE_TRUE"
                rawTrace = request.headers.get(self.traceHeaderName).split('/')
                trace_id = rawTrace[0]
                TRACE = "projects/{project_id}/traces/{trace_id}".format(
                  project_id=os.getenv('GOOGLE_CLOUD_PROJECT'),
                  trace_id=trace_id)
                if ( len(rawTrace) > 1):
                    SPAN = rawTrace[1].split(';')[0]
                logging.error(request.headers.get(self.traceHeaderName))
                logging.error(rawTrace)
                logging.error(trace_id)
                logging.error(TRACE)
                logging.error(SPAN)

            # https://github.com/googleapis/googleapis/blob/master/google/logging/type/http_request.proto
            REQUEST = {
                'requestMethod': request.method,
                'requestUrl': request.url,
                'status': response.status_code,
                'responseSize': response.content_length,
                'latency': g.request_time(),
                'remoteIp': request.remote_addr,
                'requestSize': request.content_length
            }

            if 'user-agent' in request.headers:
                REQUEST['userAgent'] = request.headers.get('user-agent')

            if request.referrer:
                REQUEST['referer'] = request.referrer

            # find the log level priority sub-messages; apply the max level to the root log message
            if len(self.mLogLevels) == 0:
                severity = logging.getLevelName(logging.INFO)
                if (response.status_code >= 400 and response.status_code < 500):
                   severity = logging.getLevelName(logging.WARNING)
                elif (response.status_code >= 500):
                   severity = logging.getLevelName(logging.ERROR)
            else:
                severity = logging.getLevelName(max(self.mLogLevels))
            self.mLogLevels=[]
            self.transport_parent.send(
                None,
                timestamp=datetime.datetime.utcnow(),
                severity=severity,
                resource=self.resource,
                labels=self.labels,
                trace=TRACE,
                span_id=SPAN,
                http_request=REQUEST)

            return response
