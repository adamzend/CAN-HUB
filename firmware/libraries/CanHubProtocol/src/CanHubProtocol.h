#pragma once
#include <stdint.h>
// CAN HUB protocol v3. No transport or sensor dependencies.
namespace CanHub {
constexpr uint8_t VERSION=3;
constexpr uint16_t ESP_STATUS_MASK=0xFFE1;
enum Status : uint16_t { OK=0, ERROR=1, INIT=1u<<5, NO_RESPONSE=1u<<6,
 TIMEOUT=1u<<7, CRC=1u<<8, COMMUNICATION=1u<<9, FORMAT=1u<<10,
 RANGE=1u<<11, OVERFLOW=1u<<12, CONFIGURATION=1u<<13,
 SATURATION=1u<<14, OTHER=1u<<15 };
struct Frame { uint16_t id; uint8_t data[8]; };
constexpr bool validNode(uint8_t id) { return id>=1 && id<=95; }
constexpr uint16_t dataId(uint8_t node,uint8_t group) { return uint16_t(node)*16+2*group; }
constexpr uint16_t heartbeatId(uint8_t node) { return 0x700+node; }
inline void word(uint8_t *p,uint16_t v) { p[0]=uint8_t(v);p[1]=uint8_t(v>>8); }
inline bool pair(uint8_t node,uint8_t group,uint8_t sequence,const int16_t *values,
                 const uint16_t *statuses,uint8_t count,Frame &data,Frame &status) {
 if(!validNode(node)||group>=8||count<1||count>3||!values||!statuses)return false;
 data={};status={};data.id=dataId(node,group);status.id=data.id+1;
 data.data[0]=status.data[0]=VERSION;data.data[1]=status.data[1]=sequence;
 for(uint8_t i=0;i<count;i++) {
  uint16_t s=statuses[i]&ESP_STATUS_MASK;
  word(data.data+2+2*i,(s&ERROR)?0:uint16_t(values[i]));
  word(status.data+2+2*i,s);
 }
 return true;
}
inline Frame heartbeat(uint8_t node,uint8_t sequence,uint32_t uptime,uint16_t flags=0) {
 Frame f={};f.id=heartbeatId(node);f.data[0]=VERSION;f.data[1]=sequence;
 word(f.data+2,flags);for(uint8_t i=0;i<4;i++)f.data[4+i]=uint8_t(uptime>>(8*i));return f;
}
// Call tick() frequently from a nonblocking loop, independently of sensor results.
// Sender must return true only if the transport accepted the frame.
template<uint8_t NODE_ID> class Node {
 static_assert(NODE_ID>=1&&NODE_ID<=95,"NODE_ID must be 1..95");
 uint8_t sequence[8]={},hb=0;uint32_t last=0;bool first=true;
public:
 template<class Sender> bool tick(uint32_t now,Sender send,uint16_t flags=0) {
  if(!first&&uint32_t(now-last)<1000)return true;
  first=false;last=now;return send(heartbeat(NODE_ID,++hb,now/1000,flags));
 }
 template<class Sender> bool publish(uint8_t group,const int16_t *values,const uint16_t *statuses,uint8_t count,Sender send) {
  if(group>=8)return false;
  Frame data,status;if(!pair(NODE_ID,group,++sequence[group],values,statuses,count,data,status))return false;
  bool a=send(data);bool b=send(status);return a&&b;
 }
};
}
