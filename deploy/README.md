# Running the collector on a server

These steps set up the collector as a systemd service on Ubuntu 24.04, so it starts at boot and restarts whenever it stops. Run them as a user with sudo. Commands that need root say so.

The service keeps code, data and keys in three places, each with its own owner.

| Path                              | What it holds                                | Owner and mode                       |
| --------------------------------- | -------------------------------------------- | ------------------------------------ |
| `/opt/chicago-lamp`               | This repository and its `.venv`              | Your user. The service only reads it |
| `/var/lib/chicago-lamp`           | The buffer of raw responses and journal rows | `chicago-lamp`, mode 700             |
| `/etc/chicago-lamp/collector.env` | CTA keys and the buffer path                 | root, mode 600                       |

Updating the code never touches the data, and the service can read its keys without being able to read the file they come from.

## 1. Check the clock and time zone data

Poll times come from the system clock, and collection dates are Chicago dates.

```bash
timedatectl
ls /usr/share/zoneinfo/America/Chicago
```

`timedatectl` must show `System clock synchronized: yes` and `NTP service: active`. If the zone file is missing, install it with `sudo apt install tzdata`.

## 2. Install uv

Install the uv version that `pyproject.toml` requires, for your own user.

```bash
curl -LsSf https://astral.sh/uv/0.13.10/install.sh | sh
uv --version
```

If `uv` is not found, open a new shell so `~/.local/bin` is on `PATH`.

## 3. Create the service user

```bash
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin chicago-lamp
```

## 4. Get the code

```bash
sudo install -d -o "$USER" -g "$USER" -m 755 /opt/chicago-lamp
git clone https://github.com/seankim658/chicago-lamp.git /opt/chicago-lamp
```

## 5. Build the environment

This installs the collector and what it needs to run, without the development tools. It uses Ubuntu's own Python 3.12, and copies every file into `.venv`, so nothing in it points into your home folder.

```bash
cd /opt/chicago-lamp
UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    uv sync --locked --no-dev --package lamp-collector --no-editable --python /usr/bin/python3.12
sudo -u chicago-lamp /opt/chicago-lamp/.venv/bin/lamp-collector --help
```

The last command must print the help text. It proves the service user can run the collector.

## 6. Add the keys

```bash
sudo install -d -m 755 /etc/chicago-lamp
sudo install -m 600 -o root -g root deploy/collector.env.example /etc/chicago-lamp/collector.env
sudoedit /etc/chicago-lamp/collector.env
```

Fill in `CTA_BUS_KEY` and `CTA_TRAIN_KEY`. Leave `CTA_GTFSRT_KEY` empty until you have one, and the GTFS-Realtime feeds are skipped. Keys are 16 or more letters and digits, with no quotes or spaces.

## 7. Start the service

```bash
sudo install -m 644 deploy/chicago-lamp-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chicago-lamp-collector
```

## 8. Check that it works

```bash
systemctl status chicago-lamp-collector
journalctl -u chicago-lamp-collector -f
```

Within a minute the log shows a line for each feed it polls, the number of bus routes loaded, and then a warning for each call that fails. Ten minutes in, it writes one summary line per feed. Stop following the log with Ctrl-C, which leaves the service running.

Then look at what it saved.

```bash
sudo ls /var/lib/chicago-lamp/buffer/raw
sudo find /var/lib/chicago-lamp/buffer/coverage -name '*.jsonl' | sort | tail -n 1 | xargs sudo tail -n 3
```

The first command lists a folder per feed. The second prints the newest journal rows, which should mostly have `"status":"ok"`.

## 9. Watch the first day

```bash
sudo du -sh /var/lib/chicago-lamp/buffer
df -h /
sudo sh -c 'cat /var/lib/chicago-lamp/buffer/coverage/journal/*/*.jsonl' | grep -vc '"status":"ok"'
```

These show the buffer's size, the free disk space, and how many call attempts were not ok. Nothing is uploaded or removed yet, so the buffer grows every day. The health check in step 10 sends an alert when less than 10 GB is left.

## 10. Turn on alerts

Every 2 minutes, a health check reads the buffer and reports to [Healthchecks.io](https://healthchecks.io), which emails you when something is wrong. The check fails when a feed has had no successful poll for 10 minutes, or when less than 10 GB of disk is free. If the reports stop coming, because the server is down or offline, Healthchecks.io emails you too. The free plan is enough.

1. Sign up at Healthchecks.io and add a check named `Chicago LAMP collector`. Set its period to 2 minutes and its grace time to 5 minutes. The Integrations page should list your email address. If it doesn't, add an email integration and turn it on for the check.
2. Copy the check's ping URL into the settings.

   ```bash
   sudoedit /etc/chicago-lamp/collector.env
   ```

   Add the line `LAMP_HEALTH_PING_URL=` followed by the URL, with no quotes. Anyone who has the URL can send reports, so keep it out of the repository, chats and screenshots.

3. Install and start the timer.

   ```bash
   cd /opt/chicago-lamp
   sudo install -m 644 deploy/chicago-lamp-health.service deploy/chicago-lamp-health.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now chicago-lamp-health.timer
   ```

4. Run the check once and read what it found.

   ```bash
   sudo systemctl start chicago-lamp-health
   sudo journalctl -u chicago-lamp-health -n 5 --no-pager
   systemctl list-timers chicago-lamp-health.timer --no-pager
   ```

   The log must end with `every feed polled successfully in the last 10 minutes`, and the check on Healthchecks.io must show as up. The timer list shows when the next run is due.

5. Send a test alert. This marks the check as down without stopping collection, and the next run, at most 2 minutes later, marks it up again.

   ```bash
   sudo sh -c '. /etc/chicago-lamp/collector.env && curl -fsS -m 10 --data-raw "test alert" "$LAMP_HEALTH_PING_URL/fail"'
   ```

   You should get a "down" email within a minute or two, then an "up" email. Never test by stopping the collector, since a gap over 10 minutes is lost data.

## Updating

```bash
cd /opt/chicago-lamp
git pull
UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    uv sync --locked --no-dev --package lamp-collector --no-editable --python /usr/bin/python3.12
sudo systemctl restart chicago-lamp-collector
```

A restart misses a poll or two of each feed, so update only when there is something to ship. If a unit file changed, install it again as in step 7 or step 10 and run `sudo systemctl daemon-reload` before restarting. The health check runs the new code on its next run, with nothing to restart.

## When something goes wrong

- **The service stopped and stays stopped.** Its settings are missing or malformed. `journalctl -u chicago-lamp-collector -n 20` names the setting, never its value. Fix `/etc/chicago-lamp/collector.env`, then run `sudo systemctl restart chicago-lamp-collector`.
- **The service keeps restarting.** A feed's thread died, or it can't start at all. The log line before each start says why. It restarts every 5 seconds and never gives up.
- **Stopping takes a while.** Polls in progress finish first, which takes up to about 20 seconds.
- **An alert says a feed has no successful poll.** Run `sudo journalctl -u chicago-lamp-collector --since '-20min' --no-pager` and look at the warnings for that feed. If the collector is running and every call fails, CTA's side or the key is the likely cause.
- **An alert says the check is down, with no failure message.** The reports stopped. Check that the server is up, then run `systemctl status chicago-lamp-health.timer` and `sudo journalctl -u chicago-lamp-health -n 20 --no-pager`. Exit status 3 means the report could not reach Healthchecks.io, and exit status 2 means a setting is wrong, often the ping URL, which must start with `https://`.
