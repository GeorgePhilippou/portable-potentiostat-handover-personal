/* EIS handover: user hardware configuration is grouped in main.c below.
 * Based on MSP430FR5994_AD5941_EIS. Boot is idle; the dashboard starts a
 * bounded frequency sweep. Measurements use paired RCAL/load DFT results.
 */
#include <msp430.h>
#include <stdint.h>
#include <math.h>
#include <float.h>
#include "ad5940_lib/ad5940.h"
#include "Impedance/Impedance.h"
#include "support/support_functions.h"
#include "ec_protocol.h"
#include "ec_control.inc"
#define APPBUFF_SIZE 512
#define EIS_BUILD_ID "HANDOVER_2026_10_13_EIS_R2"
#define EIS_SCHEMA "MSP430_EIS_UART_V2"
#define EIS_MIN_FREQUENCY_MHZ 100
#define EIS_MAX_FREQUENCY_MHZ 200000000
/* Datasheet Eq. 11: 808.8 mV × buffer gain 2 × DAC attenuation 0.2.
 * The original application converts DacVoltPP using an 800 mV denominator.
 * This corrects nominal programmed amplitude; it is not amplitude calibration. */
#define EIS_DAC_PATH_SCALE (808.8f / 800.0f * 2.0f * 0.2f)
#pragma DATA_SECTION(AppBuff, ".TI.persistent")
uint32_t AppBuff[APPBUFF_SIZE];
static float LFOSCFreq;
static uint8_t eis_ready=0, eis_running=0;
static uint32_t eis_index=0, eis_sweep=0, eis_repeats=1, eis_period_ms=1000;
static uint32_t eis_amplitude_mvpp=20;
static volatile uint32_t eis_clock_ms=0;
static uint32_t eis_origin_ms=0,eis_trigger_ms=0,eis_timeout_ms=0;
static uint8_t eis_pending=0;
static float eis_requested_hz=0;
static uint32_t EISMillis(void)
{
    uint16_t gie=__get_SR_register() & GIE;
    uint32_t value;
    __disable_interrupt();value=eis_clock_ms;
    if(gie)__enable_interrupt();return value;
}
#pragma vector=TIMER0_A0_VECTOR
__interrupt void EISTimer(void){eis_clock_ms++;}

void initUart()
{
    UCA0CTLW0 = UCSWRST;                       // Put eUSCI in reset
    UCA0CTLW0 |= UCSSEL__SMCLK;                // Select SMCLK
    UCA0BRW = 104;                             // Baud rate divisor for 9600 baud
    UCA0MCTLW = UCOS16 | 0xD600 | (0x02 << 4); // UCBRSx=0xD6, UCBRFx=2
    UCA0CTLW0 &= ~UCSWRST;                     // Initialize eUSCI
    UCA0IE |= UCRXIE;                          // Enable USCI_A0 RX interrupt
}

void writeChar(char b)
{
    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = b;
}

void writeNewLine()
{
    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = '\n';

    while (!(UCA0IFG & UCTXIFG))
        ;
    UCA0TXBUF = '\r';
}

void initGPIO()
{
    ///////////////////////// Configure UART pins (UCA0 in UART mode)
    // P2.0 - UCA0TXD
    // P2.1 - UCA0RXD
    P2SEL0 &= ~(BIT0 | BIT1); // Clear P2SEL0 for pins 0 and 1
    P2SEL1 |= BIT0 | BIT1;    // Set P2SEL1 for pins 0 and 1

    /////////////////////////////////////////////////////////////////////

    // Configure P1.0 and P1.1 as outputs (LEDs) FOR DEBUGGING
    P1DIR |= BIT0 | BIT1;
    P1OUT &= ~(BIT0 | BIT1); // Initially turn off P1.0 and P1.1

    //    // ---- Analogue switch control: P2.3 ----  ///// new
    P2SEL0 &= ~BIT3;
    P2SEL1 &= ~BIT3;
    P2DIR |= BIT3;

    P2OUT |= BIT3;

    /////////////////////// Disable the GPIO power-on default high-impedance mode to activate
    /// previously configured port settings
    PM5CTL0 &= ~LOCKLPM5;

}

void initClockTo16MHz()
{
    // Configure one FRAM wait state as required by the data sheet for MCLK
    // operation beyond 8 MHz _before_ configuring the clock system.
    FRCTL0 = FRCTLPW | NWAITS_1;

    // configure the XTAL input pins
    PJSEL0 |= BIT4 + BIT5;

    // Clock System Setup
    CSCTL0_H = CSKEY >> 8;        // Unlock CS registers (move the CS KEY up 8 bits)
    CSCTL1 = DCORSEL | DCOFSEL_4; // Set DCO to 16MHz
    CSCTL2 = SELA__LFXTCLK | SELS__DCOCLK | SELM__DCOCLK;
    CSCTL3 = DIVA_0 | DIVS__1 | DIVM__1; // Set all dividers

    //    CSCTL4 &= ~LFXTOFF; //enable the ACLK
    //    do{
    //        CSCTL5 &= ~LFXTOFFG;                  // Clear XT1 fault flag
    //        SFRIFG1 &= ~OFIFG;
    //    }while (SFRIFG1&OFIFG);                   // Test oscillator fault flag
    //
    CSCTL0_H = 0; // Lock CS registers
}

static int32_t AD5940PlatformCfg(void)
{
    CLKCfg_Type clk_cfg;
    SEQCfg_Type seq_cfg;
    FIFOCfg_Type fifo_cfg;
    AGPIOCfg_Type gpio_cfg;
    LFOSCMeasure_Type LfoscMeasure;

    /* Use hardware reset */
    AD5940_HWReset();
    AD5940_Initialize(); /* Call this right after AFE reset */
    /* Platform configuration */
    /* Step1. Configure clock */
    clk_cfg.HFOSCEn = bTRUE;
    clk_cfg.HFXTALEn = bFALSE;
    clk_cfg.LFOSCEn = bTRUE;
    clk_cfg.HfOSC32MHzMode = bFALSE;
    clk_cfg.SysClkSrc = SYSCLKSRC_HFOSC;
    clk_cfg.SysClkDiv = SYSCLKDIV_1;
    clk_cfg.ADCCLkSrc = ADCCLKSRC_HFOSC;
    clk_cfg.ADCClkDiv = ADCCLKDIV_1;
    AD5940_CLKCfg(&clk_cfg);
    /* Step2. Configure FIFO and Sequencer*/
    /* Configure FIFO and Sequencer */
    fifo_cfg.FIFOEn = bTRUE; /* We will enable FIFO after all parameters configured */
    fifo_cfg.FIFOMode = FIFOMODE_FIFO;
    fifo_cfg.FIFOSize = FIFOSIZE_2KB; /* 2kB for FIFO, The reset 4kB for sequencer */
    fifo_cfg.FIFOSrc = FIFOSRC_SINC3; /* */
    fifo_cfg.FIFOThresh = 4;          /*  Don't care, set it by application paramter */
    AD5940_FIFOCfg(&fifo_cfg);
    seq_cfg.SeqMemSize = SEQMEMSIZE_4KB; /* 4kB SRAM is used for sequencer, others for data FIFO */
    seq_cfg.SeqBreakEn = bFALSE;
    seq_cfg.SeqIgnoreEn = bTRUE;
    seq_cfg.SeqCntCRCClr = bTRUE;
    seq_cfg.SeqEnable = bFALSE;
    seq_cfg.SeqWrTimer = 0;
    AD5940_SEQCfg(&seq_cfg);
    /* Step3. Interrupt controller */
    AD5940_INTCCfg(AFEINTC_1, AFEINTSRC_ALLINT,
                   bTRUE); /* Enable all interrupt in INTC1, so we can check INTC flags */
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    AD5940_INTCCfg(AFEINTC_0, AFEINTSRC_DATAFIFOTHRESH | AFEINTSRC_ENDSEQ | AFEINTSRC_CUSTOMINT0,
                   bTRUE);
    AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    /* Step4: Configure GPIO */
    gpio_cfg.FuncSet =
        GP0_INT | GP1_SLEEP |
        GP2_SYNC; /* GPIO1 indicates AFE is in sleep state. GPIO2 indicates ADC is sampling. */
    gpio_cfg.InputEnSet = 0;
    gpio_cfg.OutputEnSet = AGPIO_Pin0 | AGPIO_Pin1 | AGPIO_Pin2;
    gpio_cfg.OutVal = 0;
    gpio_cfg.PullEnSet = 0;
    AD5940_AGPIOCfg(&gpio_cfg);
    /* Measure LFOSC frequency */
    /**@note Calibrate LFOSC using system clock. The system clock accuracy decides measurement
     * accuracy. Use XTAL to get better result. */
    LfoscMeasure.CalDuration = 1000.0;        /* 1000ms used for calibration. */
    LfoscMeasure.CalSeqAddr = 0;              /* Put sequence commands from start address of SRAM */
    LfoscMeasure.SystemClkFreq = 16000000.0f; /* 16MHz in this firmware. */
    if (AD5940_LFOSCMeasure(&LfoscMeasure, &LFOSCFreq) != AD5940ERR_OK)
        return AD5940ERR_TIMEOUT;
    // printf("LFOSC Freq:%f\n", LFOSCFreq);
    AD5940_SleepKeyCtrlS(SLPKEY_UNLOCK);
    return 0;
}


/* ==================== EIS HARDWARE CONFIGURATION ====================
 * Edit hardware choices here. Frequency limits remain conservative for the
 * full range: 0.1 Hz–200 kHz, with adaptive clock, filter and DFT windows.
 * Start applies the dashboard's frequency grid, excitation and point interval.
 */
void AD5940ImpedanceStructInit(void)
{
    AppIMPCfg_Type *cfg;
    AppIMPGetCfg(&cfg);
    cfg->SeqStartAddr=0x10; /* Reserve LFOSC calibration commands. */
    cfg->MaxSeqLen=512-0x10;
    cfg->SysClkFreq=16000000.0f;
    cfg->AdcClkFreq=16000000.0f;
    cfg->WuptClkFreq=LFOSCFreq;
    cfg->RcalVal=200.0f; /* Version 2 populated RCAL. */

    /* ELECTRODE / SWITCH ROUTING: same as the original EIS experiment.
     * J3-1 RE, J3-2 CE, J3-3 WE/SE0. Join RE and CE for a two-terminal load. */
    cfg->DswitchSel=SWD_CE0;
    cfg->PswitchSel=SWP_RE0;
    cfg->NswitchSel=SWN_SE0;
    cfg->TswitchSel=SWT_SE0LOAD;

    /* ADC INPUTS: keep the paired HSTIA path for impedance ratios.
     * Internal reference voltage diagnostics belong in the OCP experiment. */
    cfg->ADCMuxP=ADCMUXP_HSTIA_P;
    cfg->ADCMuxN=ADCMUXN_HSTIA_N;
    // cfg->AdcPgaGain=ADCPGA_1;
    cfg->AdcPgaGain=ADCPGA_1P5;

    /* HSTIA / EXCITATION GAIN: gain remains fixed throughout the sweep.
     * Increasing it must preserve headroom during the 200-ohm RCAL reading. */
    // cfg->HstiaRtiaSel=HSTIARTIA_5K;
    cfg->HstiaRtiaSel=HSTIARTIA_10K;
    cfg->ExcitBufGain=EXCITBUFGAIN_2;
    cfg->HsDacGain=HSDACGAIN_0P2;
    cfg->HsDacUpdateRate=7;
    cfg->DacVoltPP=(float)eis_amplitude_mvpp/EIS_DAC_PATH_SCALE;
    cfg->BiasVolt=0.0f; /* No DC electrode bias in this validation. */
    cfg->PwrMod=AFEPWR_LP;

    /* FILTER / DFT: these main.c choices are used at every frequency.
     * 800 kSPS / SINC3 OSR 4 = 200 kSPS into the 16384-point DFT.
     * At 100 Hz this window covers approximately 8.19 cycles. */
    cfg->ADCSinc3Osr=ADCSINC3OSR_4;
    cfg->ADCSinc2Osr=ADCSINC2OSR_22;
    cfg->DftSrc=DFTSRC_SINC3;
    cfg->DftNum=DFTNUM_16384;
    cfg->HanWinEn=bTRUE;
    cfg->ADCAvgNum=ADCAVGNUM_16;
    cfg->FifoThresh=4; /* Exactly one RCAL/load DFT pair per point. */
    cfg->NumOfData=-1;
    cfg->ImpODR=1000.0f/(float)eis_period_ms;
    cfg->SinFreq=1000.0f;
    cfg->SweepCfg.SweepEn=bFALSE; /* MCU triggers one paired measurement at a time. */
    cfg->SweepCfg.SweepStart=1000.0f;
    cfg->SweepCfg.SweepStop=20000.0f;
    cfg->SweepCfg.SweepPoints=16;
    cfg->SweepCfg.SweepLog=bTRUE; // bFALSE selects a linear frequency grid.
    cfg->SweepCfg.SweepIndex=0;
}

static void EISState(const char *state)
{
    writeStringUART("# EIS_STATE,");writeStringUART(state);writeNewLine();
}
static void EISError(const char *reason)
{
    writeStringUART("# EIS_ERROR,");writeStringUART(reason);writeNewLine();
}
static void EISMetadata(void)
{
    AppIMPCfg_Type *cfg;AppIMPGetCfg(&cfg);
    writeStringUART("# EIS_META_BUILD_ID," EIS_BUILD_ID);writeNewLine();
    writeStringUART("# EIS_META_SCHEMA," EIS_SCHEMA);writeNewLine();
    writeStringUART("# EIS_META_UART_BAUD,9600");writeNewLine();
    writeStringUART("# EIS_META_FREQUENCY_START_HZ,");writeFloatUART(cfg->SweepCfg.SweepStart);
    writeStringUART("# EIS_META_FREQUENCY_STOP_HZ,");writeFloatUART(cfg->SweepCfg.SweepStop);
    writeStringUART("# EIS_META_POINTS,");writeUIntUART(cfg->SweepCfg.SweepPoints);writeNewLine();
    writeStringUART("# EIS_META_REPEATS,");writeUIntUART(eis_repeats);writeNewLine();
    writeStringUART("# EIS_META_LOG_GRID,");writeUIntUART(cfg->SweepCfg.SweepLog);writeNewLine();
    writeStringUART("# EIS_META_EXCITATION_MVPP,");writeUIntUART(eis_amplitude_mvpp);writeNewLine();
    writeStringUART("# EIS_META_SAMPLE_PERIOD_MS,");writeUIntUART(eis_period_ms);writeNewLine();
    writeStringUART("# EIS_META_RCAL_OHM,");writeFloatUART(cfg->RcalVal);
    writeStringUART("# EIS_META_TIME_SOURCE,MSP430_MILLISECOND_TIMER_RELATIVE_SWEEP_START");writeNewLine();
    writeStringUART("# EIS_META_PHASE_CONVENTION,Z_PHASE_DEGREES");writeNewLine();
    writeStringUART("# EIS_META_DFT_SIGN,NEGATIVE_IMAGINARY_OUTPUT");writeNewLine();
    writeStringUART("# EIS_META_IMPEDANCE_UNITS,ohm");writeNewLine();
    writeStringUART("# EIS_META_MEASUREMENT,PAIRED_RCAL_LOAD_CURRENT_RATIO");writeNewLine();
}
static void EISStop(const char *reason)
{
    AD5940_SEQCtrlS(bFALSE);AppIMPCtrl(IMPCTRL_SHUTDOWN,0);
    AD5940_AFECtrlS(AFECTRL_ALL,bFALSE);
    AD5940_ClrMCUIntFlag();
    if(eis_running){writeStringUART("# EIS_RUN_END,");writeStringUART(reason);writeNewLine();}
    eis_running=0;eis_pending=0;EISState(eis_ready?"IDLE":"FAULT");
}
/* Adaptive acquisition configuration remains here with the experiment settings.
 * Low frequency: sinc2 decimation gives >=8 cycles in the DFT window.
 * High frequency: raw ADC avoids sinc3 attenuation/aliasing at 200 kHz.
 * Paired windows are triggered manually: long points cannot overflow WUPT.
 */
static int32_t EISBeginPoint(AppIMPCfg_Type *cfg)
{
    CLKCfg_Type clk;
    ClksCalInfo_Type window;
    uint32_t ticks,word,scale;
    float fraction=(float)eis_index/(float)(cfg->SweepCfg.SweepPoints-1);
    float sample_rate=200000.0f;
    static const uint16_t osr[]={22,44,89,178,267,533,640,667,800,889,1067,1333};
    uint32_t i;
    eis_requested_hz=cfg->SweepCfg.SweepLog?
        cfg->SweepCfg.SweepStart*powf(cfg->SweepCfg.SweepStop/cfg->SweepCfg.SweepStart,fraction):
        cfg->SweepCfg.SweepStart+(cfg->SweepCfg.SweepStop-cfg->SweepCfg.SweepStart)*fraction;
    cfg->SysClkFreq=eis_requested_hz>=80000.0f?32000000.0f:16000000.0f;
    cfg->AdcClkFreq=cfg->SysClkFreq;
    clk.HFOSCEn=bTRUE;clk.HFXTALEn=bFALSE;clk.LFOSCEn=bTRUE;
    clk.HfOSC32MHzMode=eis_requested_hz>=80000.0f?bTRUE:bFALSE;
    clk.SysClkSrc=SYSCLKSRC_HFOSC;clk.SysClkDiv=SYSCLKDIV_1;
    clk.ADCCLkSrc=ADCCLKSRC_HFOSC;clk.ADCClkDiv=ADCCLKDIV_1;
    AD5940_WakeUp(10);AD5940_CLKCfg(&clk);
    cfg->PwrMod=eis_requested_hz>=80000.0f?AFEPWR_HP:AFEPWR_LP;
    AD5940_AFEPwrBW(cfg->PwrMod,eis_requested_hz>=80000.0f?AFEBW_250KHZ:AFEBW_100KHZ);
    cfg->HsDacUpdateRate=eis_requested_hz>=80000.0f?7:0x1b;
    cfg->ADCSinc3Osr=ADCSINC3OSR_4;cfg->DftSrc=DFTSRC_SINC3;
    cfg->ADCSinc2Osr=ADCSINC2OSR_22;
    if(eis_requested_hz>=20000.0f){cfg->DftSrc=DFTSRC_ADCRAW;sample_rate=cfg->AdcClkFreq/20.0f;}
    else if(eis_requested_hz<100.0f)
    {
        cfg->DftSrc=DFTSRC_SINC2NOTCH;
        for(i=0;i<12;i++)if(16384.0f*eis_requested_hz*osr[i]/200000.0f>=8.0f)break;
        if(i>=12)return AD5940ERR_PARA;
        cfg->ADCSinc2Osr=i;sample_rate=200000.0f/osr[i];
    }
    cfg->DftNum=DFTNUM_16384;
    /* Reduce the high-frequency window while retaining at least eight cycles. */
    if(eis_requested_hz>=100.0f)
        while(cfg->DftNum>DFTNUM_1024 && (float)(1UL<<(cfg->DftNum+1))*eis_requested_hz/sample_rate>=8.0f)cfg->DftNum--;
    word=AD5940_WGFreqWordCal(eis_requested_hz,cfg->SysClkFreq);
    scale=AD5940_WGFreqWordCal(cfg->SysClkFreq/67108864.0f,cfg->SysClkFreq);
    cfg->SinFreq=(float)word*cfg->SysClkFreq/(67108864.0f*(float)scale);
    cfg->SweepCurrFreq=cfg->SinFreq;cfg->FreqofData=cfg->SinFreq;
    cfg->bParaChanged=bTRUE;cfg->StopRequired=bFALSE;
    if(AppIMPInit(AppBuff,APPBUFF_SIZE)!=AD5940ERR_OK)return AD5940ERR_APPERROR;
    window.DataType=DATATYPE_DFT;window.DftSrc=cfg->DftSrc;
    window.DataCount=1UL<<(cfg->DftNum+2);window.ADCSinc2Osr=cfg->ADCSinc2Osr;
    window.ADCSinc3Osr=cfg->ADCSinc3Osr;window.ADCAvgNum=cfg->ADCAvgNum;
    window.BpNotch=bTRUE;window.RatioSys2AdcClk=1.0f;
    AD5940_ClksCalculate(&window,&ticks);
    eis_timeout_ms=(uint32_t)(2.0f*(float)ticks/cfg->SysClkFreq*1000.0f)+5000UL;
    writeStringUART("# EIS_POINT_BEGIN,");writeFloatPlainUART(eis_requested_hz);writeChar(',');
    writeFloatPlainUART(cfg->SinFreq);writeChar(',');writeUIntUART(eis_timeout_ms);writeNewLine();
    AD5940_ClrMCUIntFlag();AD5940_INTCClrFlag(AFEINTSRC_ALLINT);
    eis_trigger_ms=EISMillis();eis_pending=1;
    AD5940_SEQCtrlS(bTRUE);AD5940_SEQMmrTrig(cfg->MeasureSeqInfo.SeqId);
    return AD5940ERR_OK;
}
static int32_t EISBeginSweep(AppIMPCfg_Type *cfg)
{
    eis_origin_ms=EISMillis();return EISBeginPoint(cfg);
}
static void EISStart(int32_t *a,uint8_t argc)
{
    AppIMPCfg_Type *cfg;AppIMPGetCfg(&cfg);
    if(!eis_ready){EISError("PLATFORM_NOT_READY");EISState("FAULT");return;}
    if(argc!=7 || eis_running || a[0]<EIS_MIN_FREQUENCY_MHZ || a[1]>EIS_MAX_FREQUENCY_MHZ ||
       a[1]<=a[0] || a[2]<2 || a[2]>100 || a[3]<2 || a[3]>40 ||
       a[4]<500 || a[4]>10000 || a[5]<1 || a[5]>10 || (a[6]!=0 && a[6]!=1))
    {EISError("INVALID_PARAMETERS_OR_RUNNING");return;}
    cfg->SweepCfg.SweepStart=(float)a[0]/1000.0f;cfg->SweepCfg.SweepStop=(float)a[1]/1000.0f;
    cfg->SweepCfg.SweepPoints=(uint32_t)a[2];cfg->SweepCfg.SweepLog=a[6]?bTRUE:bFALSE;
    eis_amplitude_mvpp=(uint32_t)a[3];eis_period_ms=(uint32_t)a[4];eis_repeats=(uint32_t)a[5];
    cfg->DacVoltPP=(float)eis_amplitude_mvpp/EIS_DAC_PATH_SCALE;
    cfg->ImpODR=1000.0f/(float)eis_period_ms;
    eis_index=0;eis_sweep=0;
    /* Emit settings before starting the timer, so metadata cannot delay acquisition. */
    EISMetadata();
    if(EISBeginSweep(cfg)!=AD5940ERR_OK){EISError("INITIALIZATION");EISStop("INITIALIZATION_ERROR");return;}
    eis_running=1;writeStringUART("# EIS_RUN_BEGIN");writeNewLine();
    writeStringUART("# EIS_SWEEP_BEGIN,0");writeNewLine();EISState("RUNNING");
}
static void EISRecord(float frequency,const fImpPol_Type *result)
{
    float phase=result->Phase*180.0f/MATH_PI;
    float real=result->Magnitude*cosf(result->Phase);
    float imag=result->Magnitude*sinf(result->Phase);
    writeStringUART("EIS,");writeUIntUART(eis_sweep);writeChar(',');writeUIntUART(eis_index);
    writeChar(',');writeUIntUART(EISMillis()-eis_origin_ms);writeChar(',');writeFloatPlainUART(frequency);
    writeChar(',');writeFloatPlainUART(real);writeChar(',');writeFloatPlainUART(imag);
    writeChar(',');writeFloatPlainUART(result->Magnitude);writeChar(',');writeFloatUART(phase);
}
int main(void)
{
    AppIMPCfg_Type *cfg;
    WDTCTL=WDTPW|WDTHOLD;
    initClockTo16MHz();initGPIO();initUart();AD5940_MCUResourceInit(0);
    TA0CCR0=1999;TA0CTL=TASSEL__SMCLK|ID__8|MC__UP|TACLR;TA0CCTL0=CCIE;
    eis_ready=(AD5940PlatformCfg()==AD5940ERR_OK);
    AD5940ImpedanceStructInit();AppIMPGetCfg(&cfg);
    if(!eis_ready)EISError("LFOSC_INITIALIZATION_TIMEOUT");
    EISMetadata();EISState(eis_ready?"IDLE":"FAULT");
    for(;;)
    {
        char command;uint8_t argc;int32_t args[EC_MAX_ARGS];
        if(ec_stop){ec_stop=0;EISStop("USER_STOP");}
        if(ec_bad_command){ec_bad_command=0;EISError("INVALID_OR_BUSY_COMMAND");}
        if(ec_take(&command,&argc,args))
        {
            if(command=='M')
            {
                if(!eis_running)EISMetadata();
                else {writeStringUART("# EIS_META_SCHEMA," EIS_SCHEMA);writeNewLine();}
                EISState(!eis_ready?"FAULT":eis_running?"RUNNING":"IDLE");
            }
            else if(command=='S')EISStart(args,argc);
        }
        if(eis_running && eis_pending && EISMillis()-eis_trigger_ms>eis_timeout_ms)
        {writeStringUART("# EIS_TIMEOUT_FIFO,");writeUIntUART(AD5940_FIFOGetCnt());writeNewLine();EISError("POINT_TIMEOUT");EISStop("MEASUREMENT_ERROR");}
        if(eis_running && !eis_pending && EISMillis()-eis_trigger_ms>=eis_period_ms)
        {if(EISBeginPoint(cfg)!=AD5940ERR_OK){EISError("POINT_INITIALIZATION");EISStop("MEASUREMENT_ERROR");}}
        if(eis_running && eis_pending && AD5940_GetMCUIntFlag())
        {
            uint32_t count=APPBUFF_SIZE;
            float frequency;
            AD5940_ClrMCUIntFlag();
            if(AppIMPISR(AppBuff,&count)!=AD5940ERR_OK)
            {EISError("DFT_OR_FIFO_ERROR");EISStop("MEASUREMENT_ERROR");continue;}
            if(count==0)continue;
            if(count!=1){EISError("AMBIGUOUS_FREQUENCY_BATCH");EISStop("MEASUREMENT_ERROR");continue;}
            AppIMPCtrl(IMPCTRL_GETFREQ,&frequency);
            EISRecord(frequency,(fImpPol_Type*)AppBuff);eis_index++;eis_pending=0;
            if(eis_index>=cfg->SweepCfg.SweepPoints)
            {
                writeStringUART("# EIS_SWEEP_END,");writeUIntUART(eis_sweep);writeChar(',');
                writeUIntUART(eis_index);writeNewLine();eis_sweep++;
                if(eis_sweep>=eis_repeats)EISStop("COMPLETED");
                else
                {
                    AppIMPCtrl(IMPCTRL_STOPNOW,0);eis_index=0;
                    if(EISBeginSweep(cfg)!=AD5940ERR_OK){EISError("RESTART");EISStop("INITIALIZATION_ERROR");}
                    else {writeStringUART("# EIS_SWEEP_BEGIN,");writeUIntUART(eis_sweep);writeNewLine();}
                }
            }
        }
    }
}
