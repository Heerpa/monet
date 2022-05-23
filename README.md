# monet

A power calibration and setting software suite. Named after a great
impressionist who could capture different intensities of light very
well.


## Installation
Depending on the hardware used, first install corresponding libraries
* Kinesis Rotation Mount: software/IOLibSuite, software/kinesis
* Thorlabs PowerMeter: software/Thorlabs.OpticalPowerMonitor

Install Anaconda and create an environment for monet
```
! conda create -n monet python=3.8
! conda activate monet
! pip install -r requirements.txt
! python setup.py develop
```


## Usage
Put powermeter head above the objective. Connect the powermeter with the
computer. Switch on laser, move it into epi illumination, make sure the
powermeter is set to the correct wavelength calibration.
* Open Anaconda Prompt
```
! python -m monet -n Voyager   (or default instead of Voyager)
(monet) config --wavelength: 561
(monet) help config
(monet) calibrate
(monet) config --wavelength: 640
(monet) calibrate
(monet) set 13
```
