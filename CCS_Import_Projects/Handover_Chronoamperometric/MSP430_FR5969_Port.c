#define EC_RX_IMPLEMENTATION
#include "ec_protocol.h"
#include <ad5940_lib/ad5940.h>
#include <msp430.h>



//////////////////////////////////////////////////////////////////////   Definitions   ///////////////////////////////////////////////

/////////////////////////////////////////////////////////////////////
// P8.0 in MCU connected to AD5941 CS
#define AD5941_CS_DIR P8DIR
#define AD5941_CS_OUT P8OUT
#define AD5941_CS_PIN BIT3

// P3.0 connected to AD5941 RESET
#define AD5941_RST_DIR P3DIR
#define AD5941_RST_OUT P3OUT
#define AD5941_RST_PIN BIT0

// P4.1 in MCU connected to AD5941 D0 GPIO (Interrupt)
#define AD5941_INT_DIR P4DIR
#define AD5941_INT_OUT P4OUT
#define AD5941_INT_PIN BIT1

#define LED_PIN BIT0 //For Debugging purposes, Toggle LED 1.0 when AD5941 INT received

//////////////////////////////////////////////////////////////////////

// interrupt indicator flag (interrupt occurred if ucInterrupted == 1)
volatile static uint8_t ucInterrupted = 0;




//////////////////////////////////////////////////////////////////////   Functions   ///////////////////////////////////////////////

/////////////////////////////////////////// clear CS pin of AD5940
void AD5940_CsClr(void)
{
    AD5941_CS_OUT &= ~AD5941_CS_PIN;
}//end

/////////////////////////////////////////// set CS pin of AD5940
void AD5940_CsSet(void)
{
    AD5941_CS_OUT |= AD5941_CS_PIN;
}//end

/////////////////////////////////////////// clear RESET pin of AD5940
void AD5940_RstClr(void)
{
    AD5941_RST_OUT &= ~AD5941_RST_PIN;
}//end

/////////////////////////////////////////// set RESET pin of AD5940
void AD5940_RstSet(void)
{
    AD5941_RST_OUT |= AD5941_RST_PIN;
}//end

/////////////////////////////////////////// return interrupt indicator flag
uint32_t AD5940_GetMCUIntFlag(void)
{
    /* GP0 interrupt is active-low. A remaining source may keep the line
     * asserted after another source was cleared, without a fresh edge. */
    return ucInterrupted || ((P4IN & AD5941_INT_PIN) == 0);
}//end

/////////////////////////////////////////// clear interrupt indicator flag
uint32_t AD5940_ClrMCUIntFlag(void)
{
    ucInterrupted = 0;
    return 1;
}//end




/////////////////////////////////////////// setup MCU peripherals (GPIO pins, SPI, interrupt)
uint32_t AD5940_MCUResourceInit(void *pCfg)
{
    /* SPI configuration based on AD5940 requirements:
     *  data size = 8 bits (UC7BIT = 0)
     *  CLK polarity = LOW (UCCKPL = 0, inactive is low CLK)
     *  CLK phase = HIGH (UCCKPH = 1, data is captured on the first UCLK edge and changed on the following edge)
     *  master mode = enabled (UCMST = 1)
     *  SYNC mode = enabled (UCSYNC = 1)
     *  first bit = Most Significant Bit (UCMSB = 1)
     *  SPI clock = SMCLK 8 MHz (should be less than 16 MHz - UCSSEL__SMCLK (8 MHz))
     *  pre-scalar setting (0x20 = 32, F(BitClock) = F(BRCLK) / 32)
     */

       // Initialize the CS pin (P8.3) as output and set it high (inactive)
       AD5941_CS_DIR |= AD5941_CS_PIN;  // Set CS pin as output
       AD5941_CS_OUT |= AD5941_CS_PIN;  // Set CS high (inactive)

       // Initialize the RESET pin (P3.0) as output and set it high (inactive)
       AD5941_RST_DIR |= AD5941_RST_PIN;  // Set RESET pin as output
       AD5941_RST_OUT |= AD5941_RST_PIN;  // Set RESET high (inactive)

       // Initialize the SPI interface UCB1
           // Configure SPI pins for UCB1
           // P5.0 - UCB1SIMO
           // P5.1 - UCB1SOMI
           // P5.2 - UCB1CLK
       P5SEL0 |= BIT0 | BIT1 | BIT2;  // Set P5.0, P5.1, P5.2 to SPI mode
       P5SEL1 &= ~(BIT0 | BIT1 | BIT2);

       // Put UCB1 into reset state
       UCB1CTLW0 |= UCSWRST;

       // Configure UCB1 SPI
       UCB1CTLW0 = UCSWRST |       // Keep UCB1 in reset state
                   UCCKPH |        // Capture data on the first UCLK edge and change on the following edge (SPI mode 0)
                   UCMSB |         // MSB first
                   UCMST |         // Master mode
                   UCSYNC |        // Synchronous mode
                   UCSSEL__SMCLK;  // Use SMCLK

       // Set SPI clock: Divide SMCLK by 32 to get 500kHz SPI clock
       UCB1BRW = 0x20;  // Divide SMCLK (16MHz) by 32

       // Release UCB1 from reset state
       UCB1CTLW0 &= ~UCSWRST;

       // We are using polling method for SPI communication, so no need to enable SPI interrupts
       // If interrupts are needed, uncomment the following line and ensure ISR is defined
       // UCB1IE |= UCRXIE;  // Enable receive interrupt

       // Initialize the interrupt for AD5941 (P4.1)

       // Configure P4.1 as input with pull-up resistor
       AD5941_INT_DIR &= ~AD5941_INT_PIN;  // Set P4.1 as input
       P4REN |= AD5941_INT_PIN;            // Enable pull-up/down resistor
       P4OUT |= AD5941_INT_PIN;            // Select pull-up resistor

       // Clear any pending interrupt flags
       P4IFG &= ~AD5941_INT_PIN;

       // Configure interrupt on falling edge (High-to-Low transition)
       P4IES |= AD5941_INT_PIN;

       // Enable interrupt for P4.1
       P4IE |= AD5941_INT_PIN;

       // Ensure global interrupts are enabled
       __enable_interrupt();

    return 0;
}//end

/////////////////////////////////////////// write and read N bytes to/from SPI port (MSP430 - AD5941)
void AD5940_ReadWriteNBytes(unsigned char *pSendBuffer, unsigned char *pRecvBuff, unsigned long length)
{
    uint32_t tx_count=0;
    uint32_t rx_count=0;
    while (tx_count < length)
    {
        // write
        while (!(UCB1IFG & UCTXIFG));
        UCB1TXBUF = *pSendBuffer++;
        tx_count ++;

        // read
        while (!(UCB1IFG & UCRXIFG));
        *pRecvBuff++ = UCB1RXBUF;
        rx_count ++;
    }//end while
}//end

/////////////////////////////////////////// delay function (resulting delay = input (t) x 10 us)
void AD5940_Delay10us(uint32_t t_input)
{
    if(t_input == 0) t_input = 1;

    uint32_t counter = 0;

    while (counter < t_input)
    {
        // delay 10 uSec
        __delay_cycles(160);
        counter = counter + 1;
    }//end

}//end

////////////////////////////////////////////////////////////////////////////////   ISR   /////////////////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////////// ISR 1: AD5941 DATA READY INTERRUPT
#pragma vector=PORT4_VECTOR
__interrupt void Port_4(void)
{
    if (P4IFG & AD5941_INT_PIN)     // Check if interrupt was caused by P4.1
    {
        ucInterrupted = 1;

        P1OUT ^= LED_PIN;       // Turn on LED1 (P1.0)
        P4IFG &= ~AD5941_INT_PIN;   // Clear interrupt flag for P4.1
    }
}

//////////////////////////////////////////////////////////////////////////// ISR 2: MSP430 BACKCHANNEL UART TO PC
#pragma vector=USCI_A0_VECTOR
__interrupt void USCI_A0_ISR(void)
{
    switch(__even_in_range(UCA0IV, USCI_UART_UCTXCPTIFG)) {
    case USCI_UART_UCRXIFG: ec_receive_byte(UCA0RXBUF); break;
    default: break;
    }
}
