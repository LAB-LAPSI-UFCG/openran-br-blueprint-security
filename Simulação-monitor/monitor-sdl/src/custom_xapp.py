
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


class Xappmonitor:
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
        self.logger = Logger(name="Xappmonitor", level=Level.DEBUG) # The name is included in each log entry, Levels: DEBUG < INFO < WARNING < ERROR
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
        self._dispatch[30004] = self._handle_react_xapp_msg

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
        xapps = {}
        total = {}
        contagem = {}
        i = 0
        while not self._shutdown:
            nome = None 
            self._receive_RMR_messages()
            xapp_list = requests.get("http://service-ricplt-appmgr-http.ricplt:8080/ric/v1/xapps")
            if i == 180:
                self.logger.info("List of registered xApps: " + str(xapp_list.json()))
                i = 0
            for j in xapp_list.json():
                for c,v in j.items():
                    if c == 'name' and v != 'xappmonitor':
                        nome = v
                        if nome != None:
                            if nome in xapps:
                                pass
                            else:
                                xapps[nome] = 0
                                total[nome] = 0
                                contagem[nome] = 0

                            analisa = self._xapp.sdl_get(namespace=nome, key="pacote")
                            if analisa == None:
                                    pass
                            else:
                                if "ataque" in analisa or "normal" in analisa:
                                    total[nome] += 1
                                    if "ataque" in analisa:
                                        xapps[nome] += 1
                                    if total[nome] >= 101:
                                        xapps[nome] = 0
                                        total[nome] = 0
                                if total[nome] == 100:
                                    per = xapps[nome]/total[nome]
                                    if per >= 0.2:
                                        contagem[nome] += 1
                                        xapps[nome] = 0
                                        total[nome] = 0
                                if contagem[nome] >= 2:
                                    self.logger.warning(f"Alert: Attack pattern detected on Xapp: {nome}")
                                    contagem[nome] = 0
                                    self._xapp.sdl_delete(namespace=nome, key="pacote")
                                    self._xapp.rmr_send(payload=f"Message of type 30003: malicious behavior on Xapp {nome}".encode(), mtype=30003)
                                                    
            i += 1
            sleep(1)
            
    
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
    
    def _handle_react_xapp_msg(self, xapp:Xapp, summary:dict, sbuf):
        rcv_payload = summary[rmr.RMR_MS_PAYLOAD].decode() # Decodes the RMR message payload
        self.logger.info("Received message of type 30004 with payload: {}".format(rcv_payload))
        xapp.rmr_free(sbuf) # Frees the RMR message buffer

    def _handle_signal(self, signum: int, frame):
        """
        Function called when a Kubernetes signal is received to stop the xApp execution.
        """
        self.logger.info("Received signal {} to stop the xApp.".format(signal.Signals(signum).name))
        self.stop() # Custom xApp termination routine
    
    def config_handler(self, name:str, path:str, data:bytes, ctype:str):
        """
        Handler for the HTTP GET /ric/v1/config request.
        """
        self.logger.info("Received GET /ric/v1/config request with content type {}.".format(ctype))
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
