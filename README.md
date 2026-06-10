# unraid-cache-mover
  Plugin to move playing Media Files from array disks to a pool device in user Share
  while watching and keep them there by rules, incl. live switching in background.

## Installation
Plugins > Install Plugin (or it can be installed from the Community Apps plugin)
```
https://raw.githubusercontent.com/alturismo/unraid-cache-mover/master/cache-mover.plg
```

## Usage
Once setted up, Media Files from Array disks (HDD) can be copied or moved to a pool ssd.

The plugin can either (switch mode options):
1) `switch` - Live switch internally to cache (/mnt/user/... access is mandatory for Media Server, Players, ...) 
2) `close` - Hard cut file access internally. Player will stall and you will know when to stop/start - Pool reading will then be active now.
3) `nothing` - Do nothing. You have to manually know when you want to stop/start to use pool instead array disks.

### Preparation
step by step through the setup options
mover tuning exclusions are builtin, if not in use, consider your mover settings (files get moved back ...)
```
Settings --> Cache Mover
```

### Usage
SettingsPage, cache-mover.
```
Cache Mover --> go through the listings
```

## Head Start - Instant Starts
For libraries that live on spun-down array disks, "Head Start" keeps just the
**first few seconds** of (most) media on the fast pool as a small *sparse* stub
truncated to the original file size. Because Unraid serves the pool copy in
preference to the array copy, a player opens the stub and starts **instantly** -
no array spin-up wait. When you actually start watching, Smart Pre-Cache fills
the rest of that file **in place** (same inode, so the open player keeps reading
it) and the file is handed to the normal keep/evict lifecycle.

How it works:
- A seeder (`cache_mover_head`, on its own cron) walks the array and creates the
  sparse heads for files matching your Media subfolders / filetypes / exclusions.
- Heads are tracked in `headstart_list` and pinned on the pool via the mover
  ignore list. They are kept out of the regular `move_excl_list`, so the cleaner
  never mistakes a sparse head for a finished copy.
- Unused heads are evicted after `headstart_keephours`.

Notes / caveats:
- **Best for direct-play containers (e.g. `.mkv`).** Some clients/containers
  probe the END of a file on open (e.g. the MP4 `moov` atom) which lands in the
  not-yet-filled sparse region - restrict `filetypes` if you hit playback issues.
- Requires Plex Smart Pre-Cache enabled to do the on-play fill.
- The seed pass spins up array disks; schedule it off-peak and use
  `Max New Heads Per Run` to throttle.

## Information
Warning, this plugin can also delete existing files, be aware about your settings and pathes
