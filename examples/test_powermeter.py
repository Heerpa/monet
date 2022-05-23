"""
INSTALLATION

install pwoermeter software
https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=OPM

INSTALL IO libraries for detecting USB device
https://www.keysight.com/en/pd-1985909/io-libraries-suite/

activate environment
$ conda activate thorpower

install VISA package
$ pip install pyvisa
and PM python library
$ pip install ThorlabsPM100

reboot computer

again, activate environment, execute python, and

>>> import pyvisa
>>> from ThorlabsPM100 import ThorlabsPM100
>>> rm = pyvisa.ResourceManager()
>>> rm.list_resources()
('USB0::0x1313::0x8078::P0012869::0::INSTR',)
>>> inst = rm.open_resource('USB0::0x1313::0x8078::P0012869::0::INSTR', term_chars='\n', timeout=1)
Traceback (most recent call last):
  File "<stdin>", line 1, in <module>
  File "C:\Users\miblab\.conda\envs\thorpower\lib\site-packages\pyvisa\highlevel.py", line 3299, in open_resource
    raise ValueError(
ValueError: 'term_chars' is not a valid attribute for type USBInstrument
>>> inst = rm.open_resource('USB0::0x1313::0x8078::P0012869::0::INSTR', timeout=1)
>>> power_meter = ThorlabsPM100(inst=inst)
>>> power_meter.read
9.13788778e-09
>>>
"""

