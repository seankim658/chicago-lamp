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

These show the buffer's size, the free disk space, and how many call attempts were not ok. Nothing is uploaded or removed yet, so the buffer grows every day. Check the free space each week.

## Updating

```bash
cd /opt/chicago-lamp
git pull
UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    uv sync --locked --no-dev --package lamp-collector --no-editable --python /usr/bin/python3.12
sudo systemctl restart chicago-lamp-collector
```

A restart misses a poll or two of each feed, so update only when there is something to ship. If the unit file changed, install it again as in step 7 and run `sudo systemctl daemon-reload` before restarting.

## When something goes wrong

- **The service stopped and stays stopped.** Its settings are missing or malformed. `journalctl -u chicago-lamp-collector -n 20` names the setting, never its value. Fix `/etc/chicago-lamp/collector.env`, then run `sudo systemctl restart chicago-lamp-collector`.
- **The service keeps restarting.** A feed's thread died, or it can't start at all. The log line before each start says why. It restarts every 5 seconds and never gives up.
- **Stopping takes a while.** Polls in progress finish first, which takes up to about 20 seconds.
