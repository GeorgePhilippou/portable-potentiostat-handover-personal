#ifndef EC_PROTOCOL_H
#define EC_PROTOCOL_H
#include <stdint.h>
#include <limits.h>
#define EC_MAX_ARGS 8
#define EC_LINE_SIZE 112
extern volatile uint8_t ec_ready, ec_bad_command, ec_stop;
extern volatile char ec_command;
extern volatile uint8_t ec_argc;
extern volatile int32_t ec_args[EC_MAX_ARGS];
/* Full-line validation: malformed/overflowing commands never become starts. */
static int ec_parse_line(const char *s, char *command, uint8_t *argc, int32_t *args)
{
    uint8_t n=0;
    char c=*s++;
    if ((c=='M' || c=='X') && *s=='\0') { *command=c; *argc=0; return 1; }
    if(c!='S' || *s++!=',') return 0;
    for (;;) {
        uint32_t v=0; uint8_t negative=0, digits=0;
        if(n>=EC_MAX_ARGS) return 0;
        if(*s=='-') {negative=1; s++;}
        while(*s>='0' && *s<='9') {
            uint32_t d=(uint32_t)(*s++-'0');
            uint32_t limit=negative ? 2147483648UL : 2147483647UL;
            if(v>(limit-d)/10UL) return 0;
            v=v*10UL+d; digits=1;
        }
        if(!digits) return 0;
        args[n++]=negative ? (v==2147483648UL ? INT32_MIN : -(int32_t)v) : (int32_t)v;
        if(*s=='\0') break;
        if(*s++!=',') return 0;
    }
    *command=c; *argc=n; return 1;
}
#ifdef EC_RX_IMPLEMENTATION
static void ec_receive_byte(char ch)
{
    static char line[EC_LINE_SIZE]; static uint8_t used=0, overflow=0;
    char command; uint8_t argc, i; int32_t args[EC_MAX_ARGS];
    if(ch=='\r') return;
    if(ch!='\n') {
        if(used<EC_LINE_SIZE-1 && !overflow) line[used++]=ch;
        else overflow=1;
        return;
    }
    line[used]='\0';
    if(used==0 && !overflow) return;
    if(overflow || !ec_parse_line(line,&command,&argc,args)) ec_bad_command=1;
    else if(command=='X') { ec_stop=1; ec_ready=0; }
    else if(ec_ready || ec_stop) ec_bad_command=1;
    else {
        ec_command=command; ec_argc=argc;
        for(i=0;i<argc;i++) ec_args[i]=args[i];
        ec_ready=1;
    }
    used=0; overflow=0;
}
#endif
#endif
