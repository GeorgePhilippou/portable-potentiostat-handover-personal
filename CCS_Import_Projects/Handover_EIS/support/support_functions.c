#include <stdint.h>
#include <stdlib.h>
#include "support_functions.h"
void writeUIntUART(uint32_t value)
{
    char digits[10]; uint8_t n=0;
    do { digits[n++]=(char)('0'+value%10UL); value/=10UL; } while(value);
    while(n) writeChar(digits[--n]);
}
void writeIntUART(int32_t value)
{
    if(value<0) {writeChar('-'); writeUIntUART((uint32_t)(-(value+1))+1UL);}
    else writeUIntUART((uint32_t)value);
}
void writeStringUART(const char *s) { while(*s) writeChar(*s++); }
void writeFloatPlainUART(float value)
{
    uint32_t whole, fraction, divisor;
    if(value!=value || value>2147483000.0f || value< -2147483000.0f) {
        writeStringUART("NaN"); return;
    }
    if(value<0) { writeChar('-'); value=-value; }
    whole=(uint32_t)value;
    fraction=(uint32_t)((value-(float)whole)*10000.0f+0.5f);
    if(fraction>=10000UL) {whole++; fraction=0;}
    writeUIntUART(whole); writeChar('.');
    for(divisor=1000UL;divisor;divisor/=10UL) writeChar((char)('0'+(fraction/divisor)%10UL));
}
void writeFloatUART(float value) {writeFloatPlainUART(value);writeNewLine();}
void writeHex32UART(uint32_t value)
{
    int i; for(i=7;i>=0;i--) writeChar("0123456789ABCDEF"[(value>>(i*4))&15UL]);
}
char *convertIntToCharArray_4(unsigned int value)
{
    char *s=malloc(5); int i;
    if(!s) return 0;
    s[4]='\0';for(i=3;i>=0;i--) {s[i]=(char)('0'+value%10);value/=10;}
    return s;
}
