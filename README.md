# VRM Auto-Scraper

Automated VRM model scraper and downloader that crawls multiple sources, downloads free models, handles various file formats, and maintains comprehensive metadata documentation.

## Service Summary (for OAuth App Registration)

**Application Name:** VRM AI Training Data Collector

**Service URL:** https://github.com/coff33ninja/vrm-auto-scraper

**App Overview:**
> This application collects freely-available VRM avatar models to build a training dataset for AI-powered avatar animation research. The goal is to develop machine learning models that can intelligently animate VRM avatars using natural language or simple inputs, eliminating the need for complex animation code, inverse kinematics loops, and manual keyframe programming.
>
> The collected model data (geometry, rigging, blend shapes) will be used to train neural networks that understand humanoid avatar structure and movement, enabling:
> - Natural pose generation from text descriptions
> - Simplified avatar control without extensive animation code
> - AI-driven motion synthesis that adapts to different avatar proportions
>
> This tool only downloads models explicitly marked as downloadable by their creators and respects all license terms. No models are redistributed; they are used solely for local AI training research.

## Features

- **Multi-source crawling**: VRoid Hub, Sketchfab, and GitHub
- **Automatic downloading**: Finds and downloads all free/available models
- **Format handling**: VRM, GLB, ZIP archives with automatic extraction
- **Metadata tracking**: Source URL, artist, acquired date, license, file info
- **SQLite storage**: Persistent database with JSON export/import
- **Rate limiting**: Respects API limits with configurable delays
- **Duplicate prevention**: Skips already-downloaded models

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and add your API credentials:

```env
# VRoid Hub OAuth 2.0 (register at hub.vroid.com/oauth/applications)
VROID_CLIENT_ID=your_application_id
VROID_CLIENT_SECRET=your_client_secret
VROID_ACCESS_TOKEN=  # obtained via oauth flow
VROID_REFRESH_TOKEN= # obtained via oauth flow

# Sketchfab API Token (from sketchfab.com/settings/password)
SKETCHFAB_API_TOKEN=your_api_token

# GitHub Token (optional, for higher rate limits)
GITHUB_TOKEN=your_github_token

# Settings
RATE_LIMIT_DELAY=1.0
DATA_DIR=./data
```

### VRoid Hub Setup

VRoid Hub uses OAuth 2.0 with PKCE. To set up:

1. Go to [VRoid Hub OAuth Applications](https://hub.vroid.com/oauth/applications)
2. Click "New Application" and fill in:
   - Application name
   - Redirect URI: `http://localhost:8910/callback`
3. Copy the Application ID and Secret to your `.env` file
4. Run the authentication command:
   ```bash
   python src/cli.py vroid-auth
   ```
5. A browser window will open for authorization
6. After authorizing, tokens will be displayed and saved

To refresh expired tokens:
```bash
python src/cli.py vroid-refresh
```

## Usage

### Initialize database
```bash
python src/cli.py init
```

### Crawl and download models
```bash
# Crawl all sources
python src/cli.py crawl

# Crawl specific sources
python src/cli.py crawl --sources github,sketchfab

# With custom keywords and limits
python src/cli.py crawl --keywords "anime,avatar" --max 50
```

### List downloaded models
```bash
python src/cli.py list
python src/cli.py list --source vroid_hub --limit 20
```

### Export/Import library
```bash
python src/cli.py export models.json
python src/cli.py import models.json
```

### View statistics
```bash
python src/cli.py stats
```

## File Structure

```
vrm-scraper/
├── src/
│   ├── cli.py           # CLI interface
│   ├── config.py        # Configuration management
│   ├── crawler.py       # Crawler engine
│   ├── storage.py       # SQLite metadata store
│   ├── archive.py       # Archive/file handler
│   └── sources/
│       ├── base.py      # Base classes & rate limiter
│       ├── vroid_hub.py # VRoid Hub API
│       ├── sketchfab.py # Sketchfab API
│       └── github.py    # GitHub API
├── tests/               # Property-based tests
├── data/                # Downloaded models & database
│   ├── raw/             # Original downloads
│   ├── extracted/       # Extracted archives
│   └── models.db        # SQLite database
└── .env                 # API tokens (create from .env.example)
```

## Metadata Tracked

Each downloaded model records:
- Source (vroid_hub, sketchfab, github)
- Model ID and name
- Artist/creator
- Source URL
- License type and URL
- Acquired timestamp
- File path, type, and size
- Notes (archive contents, conversion instructions)

## GLB Conversion

Sketchfab provides GLB files (not VRM). The scraper attaches conversion notes:
- **Blender**: Use [VRM Add-on for Blender](https://vrm-addon-for-blender.info/)
- **Unity**: Use [UniVRM](https://github.com/vrm-c/UniVRM)

## Running Tests

```bash
pytest tests/ -v
```

## License

MIT
