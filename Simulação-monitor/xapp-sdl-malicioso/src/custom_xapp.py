
# Imports from OSC libraries
from ricxappframe.xapp_frame import Xapp, rmr
from mdclogpy import Logger, Level
from ricxappframe import xapp_rest

# Imports from other libraries
from time import sleep
from threading import Thread
import signal
import json
import requests
import random

class XappSdlMalicioso:
    """
    Custom xApp class.

    Parameters
    ----------
    thread: bool = True
        Flag for executing the xApp loop as a thread. Default is True.
    """
    def __init__(self, thread:bool = True):
        """
        Initializes the custom xApp instance and instatiates the xApp framework object.
        """
        
        # Initializing a logger for the custom xApp instance in Debug level (logs everything)
        self.logger = Logger(name="xappsdlmalicioso", level=Level.DEBUG) # The name is included in each log entry, Levels: DEBUG < INFO < WARNING < ERROR
        #self.logger.get_env_params_values() # Getting the MDC key-value pairs from the environment
        self.logger.info("Initializing the xApp.")

        # Instatiating the xApp framework object 
        self._xapp = Xapp(entrypoint=self._entrypoint, # Custom entrypoint for starting the framework xApp object
                                 rmr_port=4560, # Port for RMR data
                                 rmr_wait_for_ready=True, # Block xApp initiation until RMR is ready
                                 use_fake_sdl=False) # Use a fake in-memory SDL

        # Registering a handler for terminating the xApp after TERMINATE, QUIT, or INTERRUPT signals
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGQUIT, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Registering handlers for RMR messages
        self._dispatch = {} # Dictionary for calling handlers of specific message types
        self._dispatch[30003] = self.active_xapp_handler

        # Initializing custom control variables
        self._shutdown = False # Stops the xApp loop if True
        self._thread = thread # True for executing the xApp loop as a thread
        self._ready = False # True when the xApp is ready to start

        # Starting a threaded HTTP server listening to any host at port 8080 
        self.http_server = xapp_rest.ThreadedHTTPServer("0.0.0.0", 8080)
        self.http_server.handler.add_handler(self.http_server.handler, method="GET", name="config", uri="/ric/v1/config", callback=self.config_handler)
        self.http_server.handler.add_handler(self.http_server.handler, method="GET", name="liveness", uri="/ric/v1/health/alive", callback=self.liveness_handler)
        self.http_server.handler.add_handler(self.http_server.handler, method="GET", name="readiness", uri="/ric/v1/health/ready", callback=self.readiness_handler)
        self.logger.info("Starting HTTP server.")
        self.http_server.start()  

        # The xApp is ready to start now
        self._ready = True
        self.logger.info("xApp is ready.")
    
    def _entrypoint(self, xapp:Xapp):
        """
        Function containing the xApp logic. Called by the xApp framework object when it executes its run() method.
        
        Parameters
        ----------
        xapp: Xapp
            This is the xApp framework object (passed by the framework).
        """ 
               
        # Starting the xApp loop
        self.logger.info("Starting xApp loop in threaded mode.")
        Thread(target=self._loop).start()

    def _loop(self):
        """
        Loops logging an increasing counter each second.
        """
        normal = "Pacote normal de dados"
        malicioso = "Pacote de ataque"
        
        while not self._shutdown: # True since custom xApp initialization until stop() is called
            self._receive_RMR_messages() # Call handlers for all received RMR messages
            dados = self._xapp.sdl_get(namespace="xappsdlmalicioso", key="pacote")
            if dados is None:
                dados = normal
            else:
                i = random.randint(1,10)
                if i == 3 or i == 7:
                    dados = malicioso
                else:
                    dados = normal
            self._xapp.sdl_set(namespace="xappsdlmalicioso", key="pacote", value=dados)
            self.logger.info("Stored message: {}".format(dados))
            

            sleep(1) # Sleep for 1 second    

    def _handle_signal(self, signum: int, frame):
        """
        Function called when a Kubernetes signal is received to stop the xApp execution.
        """
        self.logger.info("Received signal {} to stop the xApp.".format(signal.Signals(signum).name))
        self.stop() # Custom xApp termination routine
    
    def active_xapp_handler(self, xapp:Xapp, summary: dict, sbuf):
        self.logger.info("Received Test-xapp RMR message with payload: {}.".format(summary[rmr.RMR_MS_PAYLOAD].decode()))
        conteudo = summary[rmr.RMR_MS_PAYLOAD].decode()
        nome = conteudo.split()[-1]
        if 'malicious' in conteudo:
            self.logger.warning(f"Suspicious xapp: {nome}, recommendation: remove it")
            
        xapp.rmr_rts(sbuf, new_payload="Received message correctly".encode(), new_mtype=30004) # Responding to the test-xapp message
        xapp.rmr_free(sbuf)
        
    def _receive_RMR_messages(self):
        """
        Call handlers for all received RMR messages.
        """
        for summary, sbuf in self._xapp.rmr_get_messages():
            func = self._dispatch.get(summary[rmr.RMR_MS_MSG_TYPE], self._default_handler)
            self.logger.debug("Invoking RMR message handler on type {}".format(summary[rmr.RMR_MS_MSG_TYPE]))
            func(self._xapp, summary, sbuf)
    
    def _default_handler(self, xapp:Xapp, summary:dict, sbuf):
        """
        Handler for RMR messages of unregistered types.
        """
        xapp.logger.info(
            "Received unknow message type {} with payload = {}".format(
                summary[rmr.RMR_MS_MSG_TYPE],
                summary[rmr.RMR_MS_PAYLOAD].decode()
            )
        )
        xapp.rmr_free(sbuf)
    
    def config_handler(self, name:str, path:str, data:bytes, ctype:str):
        """
        Handler for the HTTP GET /ric/v1/config request.
        """
        response = xapp_rest.initResponse(
            status=200, # Status = 200 OK
            response="Config data"
        ) # Initiating HTTP response
        response['payload'] = json.dumps(self._xapp._config_data) # Payload = the xApp config-file
        self.logger.debug("Config handler response: {}.".format(response))
        return response

    def liveness_handler(self, name:str, path:str, data:bytes, ctype:str):
        """
        Handler for the HTTP GET /ric/v1/health/alive request.
        """
        if self._xapp.healthcheck():
            response = xapp_rest.initResponse(
                status=200, # Status = 200 OK
                response="Liveness"
            ) # Initiating HTTP response
            response['payload'] = json.dumps({"status": "Healthy"}) # Payload = status: Healthy
        else:
            response = xapp_rest.initResponse(
                status=503, # Status = 503 Service Unavailable
                response="Liveness"
            )
            response['payload'] = json.dumps({"status": "Unhealthy"}) # Payload = status: Unhealthy
        self.logger.debug("Liveness handler response: {}.".format(response['payload']))
        return response

    def readiness_handler(self, name:str, path:str, data:bytes, ctype:str):
        """
        Handler for the HTTP GET /ric/v1/health/ready request.
        """
        if self._ready:
            response = xapp_rest.initResponse(
                status=200, # Status = 200 OK
                response="Readiness"
            ) # Initiating HTTP response
            response['payload'] = json.dumps({"status": "Ready"}) # Payload = status: Healthy
        else:
            response = xapp_rest.initResponse(
                status=503, # Status = 503 Service Unavailable
                response="Readiness"
            )
            response['payload'] = json.dumps({"status": "Not ready"})
        self.logger.debug("Readiness handler response: {}.".format(response['payload']))
        return response

    def start(self):
        """
        Starts the xApp loop.
        """ 
        self._xapp.run()

    def stop(self):
        """
        Terminates the xApp. Can only be called if the xApp is running in threaded mode.
        """
        self._shutdown = True
        self.logger.info("Calling framework termination to unregister the xApp from AppMgr.")
        self._xapp.stop()
        self.http_server.stop()