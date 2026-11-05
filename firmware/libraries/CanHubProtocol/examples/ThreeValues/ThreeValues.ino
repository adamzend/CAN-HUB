// zbudowane przez Codex
// Three signed values and independent mandatory heartbeat; MCP2515 8MHz, CAN 125kbps.
#include <SPI.h>
#include <mcp2515.h>
#include <CanHubProtocol.h>
constexpr uint8_t NODE_ID=4; // Change this to a free ID (1..95).
CanHub::Node<NODE_ID> node;
MCP2515 can(27,100000,&SPI); // CS=27, SPI=100kHz for the tested TXS0108E wiring.
bool ready=false;
uint32_t lastInit=0,lastSample=0;
bool sendFrame(const CanHub::Frame &f){
 if(!ready)return false;
 can_frame frame={};frame.can_id=f.id;frame.can_dlc=8;memcpy(frame.data,f.data,8);
 return can.sendMessage(&frame)==MCP2515::ERROR_OK;
}
void setup(){
 Serial.begin(115200);
 pinMode(25,OUTPUT);digitalWrite(25,HIGH); // TXS0108E OE; A=3.3V, B=5V.
 SPI.begin(18,19,23,27); // SCK, MISO, MOSI, CS.
}
void loop(){
 uint32_t now=millis();
 if(!ready&&(lastInit==0||now-lastInit>=2000)){
  lastInit=now;can.reset();
  ready=can.setBitrate(CAN_125KBPS,MCP_8MHZ)==MCP2515::ERROR_OK && can.setNormalMode()==MCP2515::ERROR_OK;
 }
 node.tick(now,sendFrame); // Heartbeat independent of sensor success; never gated by status.
 if(ready&&now-lastSample>=2500){
  lastSample=now;
  int16_t values[3]={-55,int16_t((now/2500)%1001),234}; // Positions 1,2,3. -55 means -5.5°C if displayed with 1 decimal.
  uint16_t status[3]={CanHub::OK,CanHub::OK,CanHub::OK};
  // Example fault: status[1]=CanHub::ERROR|CanHub::CRC; only position 2 becomes zero.
  if(!node.publish(0,values,status,3,sendFrame))Serial.println("CAN transmit failed");
 }
}
