#ifndef SUPPORT_FUNCTIONS_H
#define SUPPORT_FUNCTIONS_H
#include <stdint.h>
void writeChar(char);
void writeNewLine(void);
void writeUIntUART(uint32_t);
void writeIntUART(int32_t);
void writeFloatPlainUART(float);
void writeFloatUART(float);
void writeStringUART(const char *);
void writeHex32UART(uint32_t);
char *convertIntToCharArray_4(unsigned int);
#endif
