/* Optional independent native ABI check. Does not open hardware or transmit.
 * MSVC: cl /I"C:\Program Files\bladeRF\include" scripts\metadata_abi.c
 */
#include <stdio.h>
#include <stddef.h>
#include <libbladeRF.h>
int main(void) {
    printf("size=%zu timestamp=%zu flags=%zu status=%zu actual_count=%zu reserved=%zu format=%d\n",
        sizeof(struct bladerf_metadata), offsetof(struct bladerf_metadata,timestamp),
        offsetof(struct bladerf_metadata,flags),offsetof(struct bladerf_metadata,status),
        offsetof(struct bladerf_metadata,actual_count),offsetof(struct bladerf_metadata,reserved),
        BLADERF_FORMAT_SC16_Q11_META);
    return !(sizeof(struct bladerf_metadata)==56 && offsetof(struct bladerf_metadata,reserved)==20 && BLADERF_FORMAT_SC16_Q11_META==2);
}
