/*
  文件名称：ESP32_IO_TEST.ino
  功能：所有可用IO循环点亮后循环熄灭
*/

#define PIN_RELAY1 16 
#define PIN_RELAY2 17
#define PIN_LED    23


void setup() 
{
 pinMode(PIN_RELAY1, OUTPUT);      //输出模式  
 pinMode(PIN_RELAY2, OUTPUT);      //输出模式
 pinMode(PIN_LED, OUTPUT);         //输出模式
 digitalWrite(PIN_RELAY1,LOW );    //IO默认关闭
 digitalWrite(PIN_RELAY2,LOW );    //IO默认关闭
 digitalWrite(PIN_LED,LOW );       //IO默认关闭
}

void loop() 
{
  digitalWrite(PIN_RELAY1,HIGH );       
  delay(100); //延时0.1S
  digitalWrite(PIN_RELAY2,HIGH );       
  delay(100); //延时0.1S
  digitalWrite(PIN_LED,HIGH );       
  delay(100); //延时0.1S
 
  
  digitalWrite(PIN_RELAY1,LOW );
  delay(100); //延时0.1S
  digitalWrite(PIN_RELAY2,LOW );
  delay(100); //延时0.1S
  digitalWrite(PIN_LED,LOW );
  delay(100); //延时0.1S
}
