/*
 * (Laboratorio de Difraccion).
 *
 * El carrito avanza hacia FIN midiendo con el LDR y regresa a HOME. Se controla
 * por Serial desde la interfaz disenada.
 *
 * Pines utilizados:
 *    D5   pin_step          pulso STEP del motor (un pulso = un paso)
 *    D4   pin_direction     sentido del motor (HIGH = hacia HOME, LOW = hacia FIN)
 *    A0   sensorPin         sensor LDR
 *    D2   pin_sw_left       final de carrera IZQUIERDO = HOME (INPUT_PULLUP, presionado = LOW)
 *    D3   pin_sw_right      final de carrera DERECHO   = FIN  (INPUT_PULLUP, presionado = LOW)
 *    D8   pin_led_red       LED rojo: estado del movimiento
 *    D7   pin_led_blue      LED azul: aviso de cambio de laser
 *    D6   pin_led_white     LED blanco: aviso de arranque del sistema
 *
 * Comandos por Serial (cada uno termina en '\n'):
 *    "1"                        ir a HOME (solo retorno, no mide)
 *    "2"                        iniciar o reanudar la toma de datos hacia FIN
 *    "S"                        pausa (el carrito se queda donde esta)
 *    "X"                        cancelar todo
 *    "ROJO"|"AZUL"|"VERDE"    elegir el laser (cambia muestras y velocidad)
 *
 * Mensajes hacia la PC:
 *    "<promedio> <paso> <desviacion>"   una linea por paso hacia FIN (cuentas del ADC)
 *    "SYSTEM_READY"                     el arranque termino
 *    "HOME_REACHED"                     llego a HOME
 *    "END_REACHED"                      llego a FIN
 *    "RETURNING_HOME"                   termino la espera en FIN y regresa solo
 *    "ERROR_MAX_STEPS_EXCEEDED"         se superaron los pasos maximos
 *
 * LED rojo: parpadea mientras el carrito se mueve (led indicador durante la toma de datos), 
 * queda fijo en pausa o al llegar a FIN, y se apaga al llegar a HOME o al cancelar.
 *
 */

// Limites
#define MAX_N 50              // tamano de dataArr: maximo de muestras por paso
#define MAX_STEPS 30000       // tope de pasos hacia FIN; si se supera, se detiene con error

// Pines del motor
const int pin_step = 5;
const int pin_direction = 4;

// Pin del sensor LDR
const int sensorPin = A0;

// Switches finales de carrera
const int pin_sw_left = 2;    // izquierdo = HOME
const int pin_sw_right = 3;   // derecho = FIN

// Designacion de los pines para los LEDs
const int pin_led_red = 8;
const int pin_led_blue = 7;
const int pin_led_white = 6;

// Datos de la medicion de cada paso
int dataArr[MAX_N];           // para guardar las lecturas del LDR del paso actual
long total = 0;               // variable acumuladora
float average_f = 0.0;        // promedio de las lecturas
float sd = 0.0;               // desviacion estandar

// Designacion del estado del sistema
String Laser = "ROJO";        // laser activo: "ROJO", "AZUL" o "VERDE"
bool running = false;         // true mientras el motor da pasos
bool paused = false;          // declarada, pero no se usa en ninguna parte del codigo
long stepCounter = 0;         // pasos hacia FIN desde HOME; vuelve a 0 al llegar a HOME
int direction = 1;            // 1 = hacia HOME (izquierda), 0 = hacia FIN (derecha)
bool systemHalted = false;    // se activa con "X"; mientras sea true, loop() sale sin leer el Serial

// Parametros que dependen del laser
int measurementCount = 50;                  // muestras del LDR por paso
unsigned long stepIntervalMicros = 1600UL;  // pausa minima entre pasos, contada desde el fin del pulso anterior
unsigned int stepPulseWidth = 800;          // duracion del pulso STEP (us)

// Retorno automatico desde FIN (limite derecho)
bool waitingToReturn = false;       // true mientras espera en FIN
unsigned long timeWaitStart = 0;    // instante (ms) en que llego a FIN

// Entrada Serial
const int SERIAL_BUF_SIZE = 32;
char serialBuf[SERIAL_BUF_SIZE];    // linea que se va armando caracter por caracter
byte serialBufPos = 0;              // posicion de escritura en serialBuf

// Caracteristica del LED rojo
bool redBlinkActive = false;        
unsigned long lastRedToggle = 0;    
unsigned long redBlinkInterval = 300;   

// Comportamiento del LED azul (secuencia al cambiar de laser: 3 s parpadeando y 2 s fijo)
bool blueSeqActive = false;             
unsigned long blueSeqStart = 0;         
unsigned long blueSeqPhase = 0;         
unsigned long blueBlinkInterval = 250;  
unsigned long lastBlueToggle = 0;       
bool blueLedState = false;              

// Tiempo para el ultimo paso del motor
unsigned long lastStepMicros = 0;

// Estados del LED rojo: apagado, parpadeo o fijo
enum RedState { RED_OFF, RED_BLINK, RED_ON };
RedState redState = RED_OFF;


// Funciones del sistema de adquisicion

// Al arrancar: LED blanco fijo 3 s y luego parpadeando 2 s, para confirmar que el sistema se activo.
// Es bloqueante, pero ocurre una sola vez, dentro de setup().
void whiteStartupSequence() {
    digitalWrite(pin_led_white, HIGH);
    delay(3000);
    unsigned long end = millis() + 2000;
    while (millis() < end) {
        digitalWrite(pin_led_white, HIGH);
        delay(250);
        digitalWrite(pin_led_white, LOW);
        delay(250);
    }
}

// Parametros de muestreo y velocidad configurados segun el laser (este se elige en las interfaces).
//   laser    muestras/paso    intervalo entre pasos    ancho de pulso
//   ROJO          50               1600 us                800 us
//   AZUL          15                150 us                450 us
//   VERDE          9                 65 us                300 us
void applyLaserSettings() {
    if (Laser == "ROJO") { 
        measurementCount = 50; 
        stepIntervalMicros = 1600; 
        stepPulseWidth = 800; 
    }
    else if (Laser == "AZUL") { 
        measurementCount = 15; 
        stepIntervalMicros = 150; 
        stepPulseWidth = 450; 
    }
    else if (Laser == "VERDE") { 
        measurementCount = 9; 
        stepIntervalMicros = 65; 
        stepPulseWidth = 300; 
    }
}

// Inicia la secuencia del LED azul
void startBlueSequence() {
    blueSeqActive = true;
    blueSeqStart = millis();
    blueSeqPhase = 0;
}

// Avanza la secuencia del LED azul sin bloquear: fase 0 = parpadeo (3 s), fase 1 = fijo (2 s), luego se apaga.
void updateBlueSequence() {
    if (!blueSeqActive) return;
    unsigned long now = millis();
    unsigned long elapsed = now - blueSeqStart;
    
    if (blueSeqPhase == 0) {
        if (now - lastBlueToggle >= blueBlinkInterval) {
            lastBlueToggle = now;
            blueLedState = !blueLedState;
            digitalWrite(pin_led_blue, blueLedState);
        }
        if (elapsed >= 3000) { 
            blueSeqPhase = 1; 
            digitalWrite(pin_led_blue, HIGH); 
            blueSeqStart = now; 
        }
    }
    else if (blueSeqPhase == 1 && elapsed >= 2000) {
        digitalWrite(pin_led_blue, LOW);
        blueSeqActive = false;
    }
}

// Aplica al LED rojo el estado que indica redState. 
void updateRedLED() {
    if (redState == RED_OFF) {
        digitalWrite(pin_led_red, LOW);
    }
    else if (redState == RED_ON) {
        digitalWrite(pin_led_red, HIGH);
    }
    else if (redState == RED_BLINK) {
        if (millis() - lastRedToggle >= redBlinkInterval) {
            lastRedToggle = millis();
            digitalWrite(pin_led_red, !digitalRead(pin_led_red));
        }
    }
}

// Lee el Serial caracter por caracter y ejecuta el comando cuando llega el '\n'.
// Una linea de mas de SERIAL_BUF_SIZE - 1 caracteres se trunca
void processSerialInput() {
    while (Serial.available()) {
        char c = Serial.read();
        
        if (c == '\n') {
            serialBuf[serialBufPos] = '\0';
            String cmd = String(serialBuf);
            cmd.trim();
            
            // El comando "1": volver a HOME. Solo retorno, no mide (sirve para reubicar el sistema mecanico).
            if (cmd == "1") {
                direction = 1;
                digitalWrite(pin_direction, HIGH);
                running = true;
                waitingToReturn = false;
                redState = RED_BLINK;
                systemHalted = false;
            }
            // El comando "2": iniciar o reanudar la toma de datos hacia FIN.
            else if (cmd == "2") {
                direction = 0;
                digitalWrite(pin_direction, LOW);
                running = true;
                redState = RED_BLINK;
                systemHalted = false;
            }
            // El comando "S": pausa. El carrito se queda donde esta y se cancela la espera de retorno.
            else if (cmd == "S") {
                running = false;
                waitingToReturn = false;
                redState = RED_ON;
            }
            // El comando "X": cancelar todo (boton de cancelar de la interfaz).
            else if (cmd == "X") {
                running = false;
                waitingToReturn = false;
                systemHalted = true;
                redState = RED_OFF;
            }
            // Cambio de laser; si el color realmente cambio, avisa con la secuencia del LED azul.
            else if (cmd == "ROJO" || cmd == "AZUL" || cmd == "VERDE") {
                String prev = Laser;
                Laser = cmd;
                applyLaserSettings();
                if (prev != Laser) startBlueSequence();
            }
            
            serialBufPos = 0;
        }
        else if (serialBufPos < SERIAL_BUF_SIZE - 1) {
            serialBuf[serialBufPos++] = c;
        }
    }
}


// setup y loop

void setup() {
    Serial.begin(9600);
    
    pinMode(pin_step, OUTPUT);
    pinMode(pin_direction, OUTPUT);
    // INPUT_PULLUP: hace que con el switch suelto el pin lea HIGH; presionado lea LOW
    pinMode(pin_sw_left, INPUT_PULLUP);
    pinMode(pin_sw_right, INPUT_PULLUP);
    
    pinMode(pin_led_red, OUTPUT);
    pinMode(pin_led_blue, OUTPUT);
    pinMode(pin_led_white, OUTPUT);
    
    applyLaserSettings();
    whiteStartupSequence();
    
    Serial.println("SYSTEM_READY");   // aviso a la PC: el arranque termino
}

// Orden de cada vuelta: Serial -> finales de carrera -> retorno automatico -> paso del motor -> LEDs.
void loop() {
    if (systemHalted) {
        digitalWrite(pin_led_red, LOW);
        return;  // cancelado con "X";
    }
    
    processSerialInput();
    
    bool leftPressed  = (digitalRead(pin_sw_left)  == LOW);
    bool rightPressed = (digitalRead(pin_sw_right) == LOW);
    
    if (running) {
        if (direction == 1 && leftPressed) {      // llego a HOME (switch izquierdo): se detiene y el contador vuelve a 0
            running = false;
            stepCounter = 0;
            redState = RED_OFF;
            Serial.println("HOME_REACHED");
        }
        
        if (direction == 0 && rightPressed) {     // llego a FIN (switch derecho): termina la toma de datos y empieza la espera
            running = false;
            waitingToReturn = true;
            timeWaitStart = millis();
            redState = RED_ON;                    // LED rojo fijo: el carrito esta parado en FIN
            Serial.println("END_REACHED");
        }
    }
    
    // Retorno automatico: 30 s despues de llegar a FIN, el carrito vuelve solo a HOME.
    if (waitingToReturn && millis() - timeWaitStart >= 30000UL) {
        waitingToReturn = false;
        direction = 1;
        digitalWrite(pin_direction, HIGH);
        running = true;
        redState = RED_BLINK;  
        Serial.println("RETURNING_HOME");
    }
    
    // Movimiento del motor: un paso cada vez que pasa stepIntervalMicros desde el fin del pulso anterior.
    if (running) {
        unsigned long nowMicros = micros();
        if (nowMicros - lastStepMicros >= stepIntervalMicros) {
            digitalWrite(pin_step, HIGH);
            delayMicroseconds(stepPulseWidth);
            digitalWrite(pin_step, LOW);
            lastStepMicros = micros();
            
            if (direction == 0) {
                stepCounter++;
                
                // Promedio de measurementCount lecturas del LDR
                total = 0;
                for (int i = 0; i < measurementCount; i++) {
                    dataArr[i] = analogRead(sensorPin);
                    total += dataArr[i];
                }
                average_f = (float)total / measurementCount;
                
                // Desviacion estandar. Nota: total es del tipo long, asi que cada termino al cuadrado
                // y la division entre measurementCount se truncan a entero.
                total = 0;
                for (int i = 0; i < measurementCount; i++) {
                    float diff = dataArr[i] - average_f;
                    total += diff * diff;
                }
                sd = sqrt(total / measurementCount);
                
                // Una linea por paso: promedio, numero de paso y desviacion
                Serial.print((int)average_f);
                Serial.print(" ");
                Serial.print(stepCounter);
                Serial.print(" ");
                Serial.println(sd);
                
                // Tope de seguridad hacia FIN: si no llega al switch, se detiene al superar la constante MAX_STEPS
                if (stepCounter > MAX_STEPS) {
                    running = false;
                    redBlinkActive = false;
                    Serial.println("ERROR_MAX_STEPS_EXCEEDED");
                }
            }
        }
    }
    
    updateRedLED();
    updateBlueSequence();
}