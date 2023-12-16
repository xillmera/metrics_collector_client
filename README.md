# Metrics collector client
Multithread app to collect data from computer sensors and send it to API hub by POST. Consist from 3 threads: App(collect data), ControlApp(wait for stop signal from web interface), BufferSender(transport to api). `send_stop_signal.py` file must be used after `send_metrics.py` to stop programm.

[Dll](https://openhardwaremonitor.org/downloads/) from [OpenHardwareMonitor](https://github.com/openhardwaremonitor/openhardwaremonitor.git) project was used.

## Dependences
```
pip install requests
pip install pythonnet
```
Must be run with administrator rights
