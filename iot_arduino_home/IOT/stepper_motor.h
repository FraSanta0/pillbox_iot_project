#ifndef STEPPERMOTOR_H
#define STEPPERMOTOR_H

#include <Stepper.h>
#include <stdlib.h>

class StepperMotor {
  private:
    const int stepsPerRevolution = 2048;  // Numero di passi per una rivoluzione completa
    const int daySteps[7] = {0, 293, 585, 878, 1170, 1462, 1755};
    Stepper myStepper;                    // Oggetto Stepper

  public:
    StepperMotor(int pin1, int pin2, int pin3, int pin4);
    
    void setSpeed(long speed);
    void stepForward(int steps);
    void stepBackward(int steps);
    int getStep(size_t index);
    int getSPR();
};

#endif