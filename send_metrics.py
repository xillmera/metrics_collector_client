
import pathlib
from dataclasses import dataclass
from time import sleep
from pathlib import Path
from datetime import datetime
import requests as req
import socket
from enum import Enum
from threading import Lock, Thread

import clr #package pythonnet, not clr
"""
OpenHardwareMonitorLib.dll поставляется в составе программы openhardwaremonitor
"""
file = 'util/OpenHardwareMonitorLib.dll'
clr.AddReference(str(pathlib.Path(file).absolute()))
from OpenHardwareMonitor import Hardware


#from pdb import Pdb
#dbr = Pdb() #отладка через консоль


"""
фоновая программа для сбора метрик компьютера и сохранения цифр в базу данных
- на компьютере в файл csv
- на удаленном сервере через запрос на API

Прогрмма состоит из трех потоков:
	App
		осуществляет сбор метрик компьютера и осуществляет запись в файл или запрос на отправку
	BufferedSender
		обрабатывает запросы на отправку
	ControlApp
		осуществляет завершение потоков при получение управлящего сигнала
			через вспомогательную программу - send_stop_signal.py

программа требует запуска с привелегиями администратора 
	требование наследуется от библиотеки С# - util/OpenHardwareMonitorLib.dll
"""

class HardwareLogData(dict):
	@classmethod
	def default(cls):
		r = cls()
		for i in ("GPU", "CPU"):
			r[i] = {"Temperature":{}, "Load":{}}
		return r

	def strip(self):
		r = self.default()
		r["measurements"] = {"Temperature":"C", "Load":"%"}
		r["CPU"]["Temperature"] = round(self["CPU"]["Temperature"]["CPU Package"],2)
		r["CPU"]["Load"] = round(self["CPU"]["Load"]["CPU Total"],2)
		r["GPU"]["Temperature"] = round(self["GPU"]["Temperature"]["GPU Core"],2)
		r["GPU"]["Load"] = round(self["GPU"]["Load"]["GPU Core"],2)
		#print(r)
# добавить логгирование в случаях дебага
		return r

	@staticmethod
	def str_manifest():
		return "CPU_TEMPERATURE;CPU_LOAD;GPU_TEMPERATURE;GPU_LOAD"

	def __repr__(self):
		return f'{self["CPU"]["Temperature"]};{self["CPU"]["Load"]};{self["GPU"]["Temperature"]};{self["GPU"]["Load"]}'.replace(".",",")
	

class HardwareDataCollector:
	"""
	Настройка и сбор метрик системы по запросу
	"""

	class HWTypes(Enum):
		CPU = Hardware.HardwareType.CPU.value__
		GpuNvidia = Hardware.HardwareType.GpuNvidia.value__
		GpuAti = Hardware.HardwareType.GpuAti.value__

	class SenSTypes(Enum):
		Temperature = Hardware.SensorType.Temperature.value__
		Load = Hardware.SensorType.Load.value__

	def __initialize_openhardwaremonitor(self):
		handle = Hardware.Computer()
		handle.CPUEnabled = True
		handle.GPUEnabled = True
		handle.Open()
		return handle

	def __init__(self):
		self.handle = self.__initialize_openhardwaremonitor()

	def collect(self):
		"""
		Собирает текущие показатели сенсоров системы
		и возвращает организованный словарь
		(тип: HardwareLogData(dict))
		"""
		res = HardwareLogData.default()
		for i in self.handle.Hardware:
			if int(i.HardwareType) == self.HWTypes.CPU.value :
				type = "CPU"
			elif int(i.HardwareType) == self.HWTypes.GpuNvidia.value or \
					int(i.HardwareType) == self.HWTypes.GpuAti.value:
				type = "GPU"
			else :
				continue
			i.Update()		
			for sensor in i.Sensors:
				# нужно достать исходный enum и его уже сохранять в класс
				if int(sensor.SensorType) == self.SenSTypes.Temperature.value or \
						int(sensor.SensorType) == self.SenSTypes.Load.value:
					s_type = str(sensor.SensorType)
				else :
					continue
				res[type][s_type][sensor.Name] = sensor.Value
		return res

class BufferedSender(Thread):
	"""
	класс для отправки накопившихся данных
	настраиваемого количества
	на http сервер методом post.

	служит для уменьшения количества запросов на сервер.

	принцип работы:
		когда очередь отправки превышает размер отправляемых данных производит отправку
		на время отправки извлекает подмассив требуемого размера из всего накопленного
		и заменяет на временную метку
		если отправка удается - удаляет метку
		если отправка отменяется - возвращает массив обратно на место метки

	взаимодействие:
		настроенный объект запускается в отдельном потоке
		через callback put_w_el добавляются данные в конец очереди отправки
	"""
	DELAY = 2
	INDICATION = False
	connector = "http://"
	tg = "192.168.1.132"
	port = "9999"
	trigger_amount = 10
	max_buffer_amount = 500
	timeout = 0.5
	max_retry_cntr = 5
	post_endpoint = "/computer_metrics_acer/"
	token = "()()()**()00uu"

	def __init__(self, bool_var, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.bv = bool_var
		self.lck = Lock()
		self.s_buffer = []
		self.retry_cntr = 0
		self.mark_cntr = 0

	@property
	def __mark(self):
		self.mark_cntr += 1
		return f"mark_{self.mark_cntr}"
	
	def put_w_el(self, el: str):
		if len(self.s_buffer) > self.max_buffer_amount:
			return # s_buffer не растет больше trigger_amount + max_retry_cntr 
		assert isinstance(el, str)
		with self.lck:
			self.s_buffer.append(el)

	def __send_data(self, send_arr , mark: str):
		headers = {"token":self.token}
		mark_pos = self.s_buffer.index(mark)
		try:
			resp = req.post(f"{self.connector}{self.tg}:{self.port}{self.post_endpoint}", json=send_arr, headers=headers, timeout=self.timeout)
			assert resp.status_code == 200
			self.s_buffer.pop(mark_pos)
			self.retry_cntr = 0 
		except BaseException:# req.exceptions.ReadTimeout:
			# любая проблема с отправкой требует возврата извлеченного массива на место
			self.retry_cntr += 1 
			self.s_buffer = self.s_buffer[:mark_pos] + send_arr + self.s_buffer[mark_pos+1:]

	def run(self, *args, **kwargs):
		while self.bv.get():
			if self.INDICATION:
				print(f"{len(self.s_buffer)}/{self.trigger_amount} - {self.retry_cntr}/{self.max_retry_cntr}") #индикация работы
			if len(self.s_buffer) >= self.trigger_amount:
				send_arr = self.s_buffer[:self.trigger_amount]
				oper_mark = self.__mark
				if not any([i.startswith("mark_") for i in send_arr]):
# можно улучшить если брать элементы дальше метки (поставить метку как начальную позицию)
# проверить - можно ли отправить интервал между метками
					self.s_buffer = [oper_mark] + self.s_buffer[self.trigger_amount:]
					self.__send_data(send_arr,  oper_mark)

			if self.retry_cntr > self.max_retry_cntr:
				print("sender not working properly")
				#break
			sleep(self.DELAY)
# альтернатива задержке расчет остатка от деления.
# так временные метки будут с одинаковыми цифрами в ед. и дробных
# сейчас разброс : 1702686207.5, 1702686208.05, 1702686208.59
# хотя ожидание установлено на 0.5 сек, но промежуточный код может выполняться разное время



class ControlVar:
	"""
	потоко-защищённая переменная типа bool
	для синхронизации остановки всех потоков
	и контролируемого завершения программы
	"""
	def __init__(self, var=False):
		self.__var = var
		self.__lock = Lock()

	def get(self):
		with self.__lock:
			return self.__var

	def set(self, val):
		with self.__lock:
			self.__var = val


class App(Thread):
	"""
	поток предназначен:
		для сбора данных с интервалом каждые DELAY секунд
		и контроле способа записи (файл, запрос на сервер)
	"""
	DELAY = 0.5
	SAVE_TO_FILE = True

	def __init__(self, bool_var, buffer_sender_callback, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.bv = bool_var
		self.bf_sd_callback = buffer_sender_callback
		self.dc = HardwareDataCollector()

	@property
	def __current_metrics(self):
		tm = str(round(datetime.now().timestamp(), 2))
		return tm + ";" + str(self.dc.collect().strip()) + "\n"

	def run(self, *args, **kwargs):
		"""
		если установлен флаг SAVE_TO_FILE
		то записывает данные в файл
		в обратном отправляет данные в BufferedSender через callback
		"""
		tg = Path("tmp/")
		tg.mkdir(exist_ok=True)

		cntr = 0
		col_Names = "TIMESTAMP;"+HardwareLogData.str_manifest()+"\n"
		if self.SAVE_TO_FILE:
			with open("tmp/data.csv", "w") as fl:
				fl.write(col_Names)
				while self.bv.get():
					cntr += 1
					try:
						fl.write(self.__current_metrics)
					except BaseException:
						self.bv.set(False)
					sleep(self.DELAY)
		else:
			while self.bv.get(): #для api есть договоренность
# лучше отправлять уже готовую модель FastAPI
				cntr += 1
				try:
					self.bf_sd_callback(self.__current_metrics)
				except BaseException:
					self.bv.set(False)
				sleep(self.DELAY)

		print(f"RESULT - total_w: {cntr}")


class ControlApp(Thread):
	"""
	поток предназначен:
		осуществляет остановку программы при подаче сигнала STOP_SIGNAL
		через сокет ('localhost', PORT)
	"""
	PORT = 8080
	STOP_SIGNAL = b'stop'

	def __init__(self, bool_var, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.bv = bool_var

	def run(self, *args, **kwargs):
		sock = socket.socket()
		sock.bind(("0.0.0.0", self.PORT))
		sock.listen(1)
		while True:
			conn, addr = sock.accept()
			try:
				print("connection found:", addr)
				data = conn.recv(1024)
				print("recieved data:", data)
				if data == self.STOP_SIGNAL:
					conn.send(b'signal accepted')
					self.bv.set(False)
					print("received STOP signal. EXIT")
					break
				conn.send(b'signal denied')
			finally:
				conn.close()


if __name__ == "__main__":
	bv = ControlVar(True)
	cap = ControlApp(bv)
	bf_sdr = BufferedSender(bv)
	ap = App(bv, bf_sdr.put_w_el)

	cap.start()
	ap.start()
	bf_sdr.start()

	cap.join()
	ap.join()
	bf_sdr.join()