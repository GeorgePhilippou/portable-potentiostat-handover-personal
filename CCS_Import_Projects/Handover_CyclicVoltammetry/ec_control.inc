/* Shared command mailbox. ISR writes only complete validated commands. */
volatile uint8_t ec_ready = 0, ec_bad_command = 0, ec_stop = 0;
volatile char ec_command = 0;
volatile uint8_t ec_argc = 0;
volatile int32_t ec_args[EC_MAX_ARGS];
static uint8_t ec_take(char *command, uint8_t *argc, int32_t *args)
{
    uint8_t i;
    __disable_interrupt();
    if (!ec_ready)
    {
        __enable_interrupt();
        return 0;
    }
    *command = ec_command;
    *argc = ec_argc;
    for (i = 0; i < *argc; i++)
        args[i] = ec_args[i];
    ec_ready = 0;
    __enable_interrupt();
    return 1;
}
