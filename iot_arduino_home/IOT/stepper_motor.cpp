#include "stepper_motor.h"

// Costruttore: inizializza l'oggetto Stepper con i pin passati
StepperMotor::StepperMotor(int pin1, int pin2, int pin3, int pin4)
  : myStepper(stepsPerRevolution, pin1, pin2, pin3, pin4) {
  // Il costruttore configura il motore con i pin dati
}

int StepperMotor::getSPR(){
  return stepsPerRevolution;
}

int StepperMotor::getStep(size_t index){
  return (index < 7) ? daySteps[index] : 2048;
}

void StepperMotor::setSpeed(long speed) {
  myStepper.setSpeed(speed);
}

void StepperMotor::stepForward(int steps) {
  myStepper.step(steps);
}

void StepperMotor::stepBackward(int steps) {
  myStepper.step(-steps);
}
