#!/bin/bash

################################
source /boot/config/plugins/cache-mover/cachemoversettings

################################
configfile="/boot/config/plugins/cache-mover/cachemoversettings"

################################
if [ ! -z "$head_cron" ]; then
( crontab -l | grep -v -F "Cache_Head" ; echo "# Cron Job for Cache_Head plugin" ) | crontab -
( crontab -l | grep -v -F "cache_mover_head" ; echo "$head_cron /usr/local/emhttp/plugins/cache-mover/scripts/cache_mover_head >/dev/null 2>&1" ) | crontab -
sed -i 's/^head_cron_state=.*/head_cron_state="started"/' $configfile
else
crontab -l | grep -v "Cache_Head"  | crontab -
crontab -l | grep -v "cache_mover_head"  | crontab -
sed -i 's/^head_cron_state=.*/head_cron_state="stopped"/' $configfile
fi

exit;
