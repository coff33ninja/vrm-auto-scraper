"""CLI interface for VRM Auto-Scraper."""
import logging
import time
from pathlib import Path
from typing import Optional

import typer

from archive import ArchiveHandler
from config import config
from crawler import CrawlerEngine
from pipeline import VRMPipeline
from sources.base import BaseSource
from sources.github import GitHubSource
from sources.sketchfab import SketchfabSource
from sources.vroid_hub import VRoidHubSource, VRoidHubOAuth
from sources.vroid_hub import save_tokens as save_vroid_tokens
from sources.vroid_hub import load_tokens as load_vroid_tokens
from sources.deviantart import DeviantArtSource, DeviantArtOAuth
from sources.deviantart import save_tokens as save_da_tokens
from sources.deviantart import load_tokens as load_da_tokens
from storage import MetadataStore, DownloadsTracker

app = typer.Typer(
    name="vrm-scraper",
    help="Automated VRM model scraper and downloader",
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_store() -> MetadataStore:
    """Get the metadata store instance."""
    config.ensure_dirs()
    return MetadataStore(config.db_path)


def get_downloads_tracker() -> DownloadsTracker:
    """Get the downloads tracker instance."""
    config.ensure_dirs()
    return DownloadsTracker(config.db_path)


def get_sources(
    include_vroid: bool = True,
    include_sketchfab: bool = True,
    include_github: bool = True,
    include_deviantart: bool = True,
) -> list[BaseSource]:
    """Get configured source instances."""
    sources: list[BaseSource] = []
    
    if include_vroid and config.has_vroid_token():
        sources.append(VRoidHubSource(
            access_token=config.vroid_access_token,
            client_id=config.vroid_client_id,
            rate_limit_delay=config.rate_limit_delay,
        ))
        logger.info("VRoid Hub source enabled")
    elif include_vroid:
        if config.has_vroid_credentials():
            logger.warning("VRoid Hub credentials found but no access token. Run 'vroid-auth' to authenticate.")
        else:
            logger.warning("VRoid Hub not configured. Set VROID_CLIENT_ID and VROID_CLIENT_SECRET in .env")
    
    if include_sketchfab and config.has_sketchfab_token():
        sources.append(SketchfabSource(
            api_token=config.sketchfab_api_token,
            rate_limit_delay=config.rate_limit_delay,
        ))
        logger.info("Sketchfab source enabled")
    elif include_sketchfab:
        logger.warning("Sketchfab token not configured, skipping")
    
    if include_deviantart and config.has_deviantart_token():
        sources.append(DeviantArtSource(
            access_token=config.deviantart_access_token,
            rate_limit_delay=config.rate_limit_delay,
        ))
        logger.info("DeviantArt source enabled")
    elif include_deviantart:
        if config.has_deviantart_credentials():
            logger.warning("DeviantArt credentials found but no access token. Run 'deviantart-auth' to authenticate.")
    
    if include_github:
        sources.append(GitHubSource(
            token=config.github_token if config.has_github_token() else None,
            rate_limit_delay=config.rate_limit_delay,
        ))
        logger.info("GitHub source enabled")
    
    return sources


@app.command()
def init():
    """Initialize the database and data directories."""
    config.ensure_dirs()
    store = get_store()
    store.close()
    typer.echo(f"Database initialized at {config.db_path}")
    typer.echo(f"Data directory: {config.data_dir}")


@app.command()
def crawl(
    keywords: Optional[str] = typer.Option(
        None,
        "--keywords", "-k",
        help="Comma-separated search keywords (default: vrm,vroid,avatar)",
    ),
    max_per_source: int = typer.Option(
        100,
        "--max", "-m",
        help="Maximum models to download per source",
    ),
    sources: Optional[str] = typer.Option(
        None,
        "--sources", "-s",
        help="Comma-separated sources to crawl: vroid,sketchfab,github (default: all)",
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help="Skip models already in database",
    ),
    force: bool = typer.Option(
        False,
        "--force", "-f",
        help="Force re-download even if already in downloads table",
    ),
):
    """Crawl sources and download VRM models."""
    # Parse keywords
    keyword_list = []
    if keywords:
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    
    # Parse sources
    include_vroid = True
    include_sketchfab = True
    include_github = True
    include_deviantart = True
    
    if sources:
        source_list = [s.strip().lower() for s in sources.split(",")]
        include_vroid = "vroid" in source_list or "vroid_hub" in source_list
        include_sketchfab = "sketchfab" in source_list
        include_github = "github" in source_list
        include_deviantart = "deviantart" in source_list or "da" in source_list
    
    # Get sources
    source_instances = get_sources(
        include_vroid=include_vroid,
        include_sketchfab=include_sketchfab,
        include_github=include_github,
        include_deviantart=include_deviantart,
    )
    
    if not source_instances:
        typer.echo("No sources available. Check your API tokens in .env file.")
        raise typer.Exit(1)
    
    # Initialize components
    config.ensure_dirs()
    store = get_store()
    downloads = get_downloads_tracker()
    archive_handler = ArchiveHandler(config.extracted_dir)
    
    crawler = CrawlerEngine(
        sources=source_instances,
        store=store,
        archive_handler=archive_handler,
        raw_dir=config.raw_dir,
        downloads_tracker=downloads,
        force_download=force,
    )
    
    typer.echo(f"Starting crawl with {len(source_instances)} source(s)...")
    typer.echo(f"Keywords: {keyword_list or ['vrm', 'vroid', 'avatar']}")
    typer.echo(f"Max per source: {max_per_source}")
    if force:
        typer.echo("Force mode: re-downloading all models")
    
    # Run crawl
    result = crawler.crawl(
        keywords=keyword_list,
        max_per_source=max_per_source,
        skip_existing=skip_existing,
    )
    
    store.close()
    downloads.close()
    
    # Report results
    typer.echo("\n--- Crawl Complete ---")
    typer.echo(f"Downloaded: {result.downloaded}")
    typer.echo(f"Skipped: {result.skipped}")
    typer.echo(f"Failed: {result.failed}")
    
    if result.errors:
        typer.echo(f"\nErrors ({len(result.errors)}):")
        for error in result.errors[:10]:  # Show first 10 errors
            typer.echo(f"  - {error}")
        if len(result.errors) > 10:
            typer.echo(f"  ... and {len(result.errors) - 10} more")


@app.command("crawl-continuous")
def crawl_continuous(
    keywords: Optional[str] = typer.Option(
        None,
        "--keywords", "-k",
        help="Comma-separated search keywords",
    ),
    batch_size: int = typer.Option(
        50,
        "--batch", "-b",
        help="Models to download per batch",
    ),
    interval: int = typer.Option(
        300,
        "--interval", "-i",
        help="Seconds between crawl batches (default: 5 minutes)",
    ),
    sources: Optional[str] = typer.Option(
        None,
        "--sources", "-s",
        help="Comma-separated sources: vroid,sketchfab,github",
    ),
    max_total: Optional[int] = typer.Option(
        None,
        "--max-total",
        help="Stop after downloading this many models total (default: unlimited)",
    ),
):
    """
    Continuously crawl sources, downloading new models as they appear.
    
    Runs in a loop, checking for new models at the specified interval.
    Press Ctrl+C to stop.
    """
    # Parse keywords
    keyword_list = []
    if keywords:
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    
    # Parse sources
    include_vroid = True
    include_sketchfab = True
    include_github = True
    include_deviantart = True
    
    if sources:
        source_list = [s.strip().lower() for s in sources.split(",")]
        include_vroid = "vroid" in source_list or "vroid_hub" in source_list
        include_sketchfab = "sketchfab" in source_list
        include_github = "github" in source_list
        include_deviantart = "deviantart" in source_list or "da" in source_list
    
    # Get sources
    source_instances = get_sources(
        include_vroid=include_vroid,
        include_sketchfab=include_sketchfab,
        include_github=include_github,
        include_deviantart=include_deviantart,
    )
    
    if not source_instances:
        typer.echo("No sources available. Check your API tokens in .env file.")
        raise typer.Exit(1)
    
    # Initialize components
    config.ensure_dirs()
    store = get_store()
    archive_handler = ArchiveHandler(config.extracted_dir)
    
    crawler = CrawlerEngine(
        sources=source_instances,
        store=store,
        archive_handler=archive_handler,
        raw_dir=config.raw_dir,
    )
    
    typer.echo(f"Starting continuous crawl with {len(source_instances)} source(s)...")
    typer.echo(f"Keywords: {keyword_list or ['vrm', 'vroid', 'avatar']}")
    typer.echo(f"Batch size: {batch_size}, Interval: {interval}s")
    if max_total:
        typer.echo(f"Will stop after {max_total} total downloads")
    typer.echo("Press Ctrl+C to stop.\n")
    
    total_downloaded = 0
    batch_num = 0
    
    try:
        while True:
            batch_num += 1
            typer.echo(f"\n=== Batch {batch_num} ===")
            
            result = crawler.crawl(
                keywords=keyword_list,
                max_per_source=batch_size,
                skip_existing=True,
            )
            
            total_downloaded += result.downloaded
            
            typer.echo(f"Batch: +{result.downloaded} downloaded, {result.skipped} skipped, {result.failed} failed")
            typer.echo(f"Total downloaded: {total_downloaded}")
            
            # Check if we've hit the max
            if max_total and total_downloaded >= max_total:
                typer.echo(f"\nReached max total ({max_total}). Stopping.")
                break
            
            # Wait for next batch
            typer.echo(f"Waiting {interval}s until next batch...")
            time.sleep(interval)
            
    except KeyboardInterrupt:
        typer.echo("\n\nStopping continuous crawl...")
    finally:
        store.close()
        typer.echo("\n--- Final Stats ---")
        typer.echo(f"Total batches: {batch_num}")
        typer.echo(f"Total downloaded: {total_downloaded}")


@app.command("list")
def list_models(
    source: Optional[str] = typer.Option(
        None,
        "--source", "-s",
        help="Filter by source (vroid_hub, sketchfab, github)",
    ),
    limit: int = typer.Option(
        50,
        "--limit", "-l",
        help="Maximum number of models to show",
    ),
):
    """List downloaded models."""
    store = get_store()
    records = store.list_all()
    store.close()
    
    if source:
        records = [r for r in records if r.source == source]
    
    records = records[:limit]
    
    if not records:
        typer.echo("No models found.")
        return
    
    typer.echo(f"{'ID':<6} {'Source':<12} {'Type':<6} {'From':<6} {'Name':<28} {'Artist':<18} {'Acquired':<12}")
    typer.echo("-" * 94)
    
    for r in records:
        name = r.name[:26] + ".." if len(r.name) > 28 else r.name
        artist = r.artist[:16] + ".." if len(r.artist) > 18 else r.artist
        acquired = r.acquired_at[:10] if r.acquired_at else ""
        orig_fmt = r.original_format or "-"
        typer.echo(f"{r.id:<6} {r.source:<12} {r.file_type:<6} {orig_fmt:<6} {name:<28} {artist:<18} {acquired:<12}")
    
    typer.echo(f"\nTotal: {len(records)} model(s)")


@app.command()
def export(
    output: str = typer.Argument(
        "models.json",
        help="Output JSON file path",
    ),
):
    """Export library to JSON file."""
    store = get_store()
    output_path = Path(output)
    store.export_json(output_path)
    count = store.count()
    store.close()
    
    typer.echo(f"Exported {count} model(s) to {output_path}")


@app.command("import")
def import_models(
    input_file: str = typer.Argument(
        ...,
        help="Input JSON file path",
    ),
):
    """Import library from JSON file."""
    store = get_store()
    input_path = Path(input_file)
    
    if not input_path.exists():
        typer.echo(f"File not found: {input_path}")
        raise typer.Exit(1)
    
    count = store.import_json(input_path)
    store.close()
    
    typer.echo(f"Imported {count} model(s) from {input_path}")


@app.command()
def stats():
    """Show library statistics."""
    store = get_store()
    records = store.list_all()
    store.close()
    
    if not records:
        typer.echo("No models in library.")
        return
    
    # Count by source
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_size = 0
    
    for r in records:
        by_source[r.source] = by_source.get(r.source, 0) + 1
        by_type[r.file_type] = by_type.get(r.file_type, 0) + 1
        total_size += r.size_bytes
    
    typer.echo("=== Library Statistics ===\n")
    typer.echo(f"Total models: {len(records)}")
    typer.echo(f"Total size: {total_size / (1024*1024):.2f} MB\n")
    
    typer.echo("By source:")
    for source, count in sorted(by_source.items()):
        typer.echo(f"  {source}: {count}")
    
    typer.echo("\nBy file type:")
    for ftype, count in sorted(by_type.items()):
        typer.echo(f"  {ftype}: {count}")


@app.command("vroid-auth")
def vroid_auth(
    port: int = typer.Option(
        8910,
        "--port", "-p",
        help="Local port for OAuth callback server",
    ),
    save_to_file: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save tokens to .vroid_tokens.json",
    ),
):
    """
    Authenticate with VRoid Hub using OAuth 2.0.
    
    This will open your browser to authorize the application.
    After authorization, tokens will be displayed and optionally saved.
    
    Prerequisites:
    1. Register your app at https://hub.vroid.com/oauth/applications
    2. Set VROID_CLIENT_ID and VROID_CLIENT_SECRET in your .env file
    3. Set redirect URI to http://localhost:8910/callback in your app settings
    """
    if not config.has_vroid_credentials():
        typer.echo("Error: VROID_CLIENT_ID and VROID_CLIENT_SECRET must be set in .env")
        typer.echo("\nTo get credentials:")
        typer.echo("1. Go to https://hub.vroid.com/oauth/applications")
        typer.echo("2. Click 'New Application'")
        typer.echo("3. Set redirect URI to: http://localhost:8910/callback")
        typer.echo("4. Copy the Application ID and Secret to your .env file")
        raise typer.Exit(1)
    
    typer.echo("Starting VRoid Hub OAuth authentication...")
    typer.echo(f"Callback URL: http://localhost:{port}/callback")
    typer.echo("\nMake sure your app's redirect URI matches this URL!")
    typer.echo("")
    
    oauth = VRoidHubOAuth(
        client_id=config.vroid_client_id,
        client_secret=config.vroid_client_secret,
        redirect_uri=f"http://localhost:{port}/callback",
    )
    
    try:
        tokens = oauth.authorize_interactive(port=port)
        
        typer.echo("\n✓ Authorization successful!\n")
        typer.echo("Add these to your .env file:\n")
        typer.echo(f"VROID_ACCESS_TOKEN={tokens.get('access_token', '')}")
        typer.echo(f"VROID_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
        
        if save_to_file:
            token_path = config.data_dir / ".vroid_tokens.json"
            save_vroid_tokens(tokens, token_path)
            typer.echo(f"\nTokens also saved to: {token_path}")
        
        typer.echo(f"\nToken expires in: {tokens.get('expires_in', 'unknown')} seconds")
        
    except Exception as e:
        typer.echo(f"\nError during authentication: {e}")
        raise typer.Exit(1)


@app.command("vroid-refresh")
def vroid_refresh():
    """
    Refresh VRoid Hub access token using stored refresh token.
    """
    if not config.has_vroid_credentials():
        typer.echo("Error: VROID_CLIENT_ID and VROID_CLIENT_SECRET must be set in .env")
        raise typer.Exit(1)
    
    refresh_token = config.vroid_refresh_token
    
    # Try loading from file if not in env
    if not refresh_token:
        token_path = config.data_dir / ".vroid_tokens.json"
        tokens = load_vroid_tokens(token_path)
        if tokens:
            refresh_token = tokens.get("refresh_token", "")
    
    if not refresh_token:
        typer.echo("Error: No refresh token found. Run 'vroid-auth' first.")
        raise typer.Exit(1)
    
    oauth = VRoidHubOAuth(
        client_id=config.vroid_client_id,
        client_secret=config.vroid_client_secret,
    )
    
    try:
        tokens = oauth.refresh_token(refresh_token)
        
        typer.echo("✓ Token refreshed successfully!\n")
        typer.echo("Update your .env file:\n")
        typer.echo(f"VROID_ACCESS_TOKEN={tokens.get('access_token', '')}")
        typer.echo(f"VROID_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
        
        token_path = config.data_dir / ".vroid_tokens.json"
        save_vroid_tokens(tokens, token_path)
        typer.echo(f"\nTokens saved to: {token_path}")
        
    except Exception as e:
        typer.echo(f"\nError refreshing token: {e}")
        raise typer.Exit(1)


@app.command("deviantart-auth")
def deviantart_auth(
    port: int = typer.Option(
        8911,
        "--port", "-p",
        help="Local port for OAuth callback server",
    ),
    save_to_file: bool = typer.Option(
        True,
        "--save/--no-save",
        help="Save tokens to .deviantart_tokens.json",
    ),
):
    """
    Authenticate with DeviantArt using OAuth 2.0.
    
    Prerequisites:
    1. Register your app at https://www.deviantart.com/developers/
    2. Set DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET in your .env file
    3. Set redirect URI to http://localhost:8911/callback in your app settings
    """
    if not config.has_deviantart_credentials():
        typer.echo("Error: DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET must be set in .env")
        typer.echo("\nTo get credentials:")
        typer.echo("1. Go to https://www.deviantart.com/developers/")
        typer.echo("2. Click 'Register Application'")
        typer.echo("3. Set redirect URI to: http://localhost:8911/callback")
        typer.echo("4. Copy the Client ID and Secret to your .env file")
        raise typer.Exit(1)
    
    typer.echo("Starting DeviantArt OAuth authentication...")
    typer.echo(f"Callback URL: http://localhost:{port}/callback")
    typer.echo("")
    
    oauth = DeviantArtOAuth(
        client_id=config.deviantart_client_id,
        client_secret=config.deviantart_client_secret,
        redirect_uri=f"http://localhost:{port}/callback",
    )
    
    try:
        tokens = oauth.authorize_interactive(port=port)
        
        typer.echo("\n✓ DeviantArt authorization successful!\n")
        typer.echo("Add these to your .env file:\n")
        typer.echo(f"DEVIANTART_ACCESS_TOKEN={tokens.get('access_token', '')}")
        typer.echo(f"DEVIANTART_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
        
        if save_to_file:
            token_path = config.data_dir / ".deviantart_tokens.json"
            save_da_tokens(tokens, token_path)
            typer.echo(f"\nTokens also saved to: {token_path}")
        
        typer.echo(f"\nToken expires in: {tokens.get('expires_in', 'unknown')} seconds")
        
    except Exception as e:
        typer.echo(f"\nError during authentication: {e}")
        raise typer.Exit(1)


@app.command("deviantart-refresh")
def deviantart_refresh():
    """Refresh DeviantArt access token using stored refresh token."""
    if not config.has_deviantart_credentials():
        typer.echo("Error: DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET must be set in .env")
        raise typer.Exit(1)
    
    refresh_token = config.deviantart_refresh_token
    
    if not refresh_token:
        token_path = config.data_dir / ".deviantart_tokens.json"
        tokens = load_da_tokens(token_path)
        if tokens:
            refresh_token = tokens.get("refresh_token", "")
    
    if not refresh_token:
        typer.echo("Error: No refresh token found. Run 'deviantart-auth' first.")
        raise typer.Exit(1)
    
    oauth = DeviantArtOAuth(
        client_id=config.deviantart_client_id,
        client_secret=config.deviantart_client_secret,
    )
    
    try:
        tokens = oauth.refresh_token(refresh_token)
        
        typer.echo("✓ DeviantArt token refreshed successfully!\n")
        typer.echo("Update your .env file:\n")
        typer.echo(f"DEVIANTART_ACCESS_TOKEN={tokens.get('access_token', '')}")
        typer.echo(f"DEVIANTART_REFRESH_TOKEN={tokens.get('refresh_token', '')}")
        
        token_path = config.data_dir / ".deviantart_tokens.json"
        save_da_tokens(tokens, token_path)
        typer.echo(f"\nTokens saved to: {token_path}")
        
    except Exception as e:
        typer.echo(f"\nError refreshing token: {e}")
        raise typer.Exit(1)


@app.command("web")
def web_viewer(
    host: str = typer.Option(
        "localhost",
        "--host", "-h",
        help="Host to bind to",
    ),
    port: int = typer.Option(
        8080,
        "--port", "-p",
        help="Port to listen on",
    ),
):
    """
    Start the web-based VRM model viewer.
    
    Opens a browser-based 3D viewer for your downloaded VRM models.
    """
    from webserver import run_server
    
    typer.echo(f"Starting VRM Viewer at http://{host}:{port}")
    typer.echo("Press Ctrl+C to stop\n")
    
    run_server(host=host, port=port)


@app.command("scan-extracted")
def scan_extracted(
    add_to_db: bool = typer.Option(
        True,
        "--add/--no-add",
        help="Add found files to database",
    ),
):
    """
    Scan extracted archives and index 3D model files.
    
    Finds FBX, Blend, OBJ, PMX, VRM, GLB files in the extracted folder
    and adds them to the database for viewing.
    """
    from datetime import datetime, timezone
    from storage import ModelRecord
    
    store = get_store()
    extracted_dir = config.extracted_dir
    
    # 3D model extensions to look for
    model_extensions = {".vrm", ".glb", ".gltf", ".fbx", ".blend", ".obj", ".pmx"}
    
    typer.echo(f"Scanning {extracted_dir} for 3D model files...")
    
    found_files: list[tuple[Path, str]] = []
    
    for ext in model_extensions:
        for file_path in extracted_dir.rglob(f"*{ext}"):
            if file_path.is_file():
                found_files.append((file_path, ext.lstrip(".")))
    
    typer.echo(f"Found {len(found_files)} 3D model files\n")
    
    if not found_files:
        store.close()
        return
    
    # Group by type
    by_type: dict[str, list[Path]] = {}
    for path, ftype in found_files:
        by_type.setdefault(ftype, []).append(path)
    
    typer.echo("By type:")
    for ftype, files in sorted(by_type.items()):
        typer.echo(f"  {ftype.upper()}: {len(files)}")
    
    if not add_to_db:
        typer.echo("\nUse --add to add these files to the database.")
        store.close()
        return
    
    typer.echo("\nAdding to database...")
    added = 0
    skipped = 0
    
    for file_path, file_type in found_files:
        # Generate a unique ID based on path
        rel_path = file_path.relative_to(extracted_dir)
        parts = rel_path.parts
        
        # Try to extract source and model_id from path
        # Expected: source/model_id/...
        source = parts[0] if len(parts) > 0 else "extracted"
        model_id = parts[1] if len(parts) > 1 else file_path.stem
        
        # Create unique source_model_id
        source_model_id = f"extracted_{model_id}_{file_path.stem}"
        
        # Check if already exists
        if store.exists(source, source_model_id):
            skipped += 1
            continue
        
        # Get file info
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            continue
        
        # Create record
        record = ModelRecord(
            source=source,
            source_model_id=source_model_id,
            name=file_path.stem,
            artist="Unknown",
            source_url="",
            license=None,
            license_url=None,
            thumbnail_path=None,
            acquired_at=datetime.now(timezone.utc).isoformat(),
            file_path=str(file_path),
            file_type=file_type,
            size_bytes=size_bytes,
            notes={"from_archive": str(rel_path.parent)},
        )
        
        try:
            store.add(record)
            added += 1
            typer.echo(f"  + {file_type.upper()}: {file_path.name}")
        except Exception as e:
            typer.echo(f"  ! Error adding {file_path.name}: {e}")
    
    store.close()
    
    typer.echo(f"\nAdded: {added}, Skipped (existing): {skipped}")


@app.command("convert")
def convert_models(
    file_type: Optional[str] = typer.Option(
        None,
        "--type", "-t",
        help="Convert only this file type (fbx, blend, obj)",
    ),
    limit: int = typer.Option(
        0,
        "--limit", "-l",
        help="Maximum number of files to convert (0 = all)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be converted without actually converting",
    ),
):
    """
    Convert FBX/Blend/OBJ files to GLB format for web preview.
    
    Requires Blender to be installed. Optionally uses FBX2glTF for faster FBX conversion.
    
    Converted files are saved alongside originals and added to the database.
    """
    from datetime import datetime, timezone
    from converter import convert_to_glb, get_converter_status
    from storage import ModelRecord
    
    # Check converter availability
    status = get_converter_status()
    
    typer.echo("=== Converter Status ===")
    if status["blender"]["available"]:
        typer.echo(f"✓ Blender: {status['blender']['path']}")
    else:
        typer.echo("✗ Blender: Not found")
        typer.echo("  Install from https://www.blender.org/download/")
    
    if status["fbx2gltf"]["available"]:
        typer.echo(f"✓ FBX2glTF: {status['fbx2gltf']['path']}")
    else:
        typer.echo("○ FBX2glTF: Not found (optional, Blender will be used)")
    
    if not status["blender"]["available"]:
        typer.echo("\nBlender is required for conversion. Please install it first.")
        raise typer.Exit(1)
    
    typer.echo("")
    
    # Get models that need conversion
    store = get_store()
    records = store.list_all()
    
    convertible_types = {"fbx", "blend", "obj"}
    if file_type:
        convertible_types = {file_type.lower()}
    
    to_convert = [r for r in records if r.file_type in convertible_types]
    
    if not to_convert:
        typer.echo("No files to convert.")
        store.close()
        return
    
    if limit > 0:
        to_convert = to_convert[:limit]
    
    typer.echo(f"Found {len(to_convert)} file(s) to convert:\n")
    
    for r in to_convert:
        typer.echo(f"  {r.file_type.upper()}: {r.name}")
    
    if dry_run:
        typer.echo("\n(Dry run - no files converted)")
        store.close()
        return
    
    typer.echo("\nConverting...")
    converted = 0
    failed = 0
    
    for r in to_convert:
        input_path = Path(r.file_path)
        
        if not input_path.exists():
            typer.echo(f"  ✗ {r.name}: File not found")
            failed += 1
            continue
        
        output_path = input_path.with_suffix(".glb")
        
        # Skip if already converted
        if output_path.exists():
            typer.echo(f"  ○ {r.name}: Already converted")
            continue
        
        result = convert_to_glb(input_path, output_path)
        
        if result:
            typer.echo(f"  ✓ {r.name}")
            converted += 1
            
            # Add converted file to database
            new_record = ModelRecord(
                source=r.source,
                source_model_id=f"{r.source_model_id}_glb",
                name=f"{r.name} (GLB)",
                artist=r.artist,
                source_url=r.source_url,
                license=r.license,
                license_url=r.license_url,
                thumbnail_path=r.thumbnail_path,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                file_path=str(output_path),
                file_type="glb",
                size_bytes=output_path.stat().st_size,
                notes={"converted_from": str(input_path)},
            )
            
            try:
                store.add(new_record)
            except Exception:
                pass  # Ignore if already exists
        else:
            typer.echo(f"  ✗ {r.name}: Conversion failed")
            failed += 1
    
    store.close()
    
    typer.echo(f"\nConverted: {converted}, Failed: {failed}")


@app.command("classify")
def classify_file(
    file_path: str = typer.Argument(
        ...,
        help="Path to file to classify",
    ),
    thumbnail: Optional[str] = typer.Option(
        None,
        "--thumbnail", "-t",
        help="Path to thumbnail image for AI classification",
    ),
    no_ai: bool = typer.Option(
        False,
        "--no-ai",
        help="Disable AI classification, use fuzzy matching only",
    ),
):
    """
    Test AI classification on a file.
    
    Shows classification result including confidence, category,
    and which strategies were used.
    """
    from classifier import ItemClassifier, check_ai_dependencies
    
    file_p = Path(file_path)
    if not file_p.exists():
        typer.echo(f"File not found: {file_path}")
        raise typer.Exit(1)
    
    thumb_p = Path(thumbnail) if thumbnail else None
    if thumb_p and not thumb_p.exists():
        typer.echo(f"Thumbnail not found: {thumbnail}")
        thumb_p = None
    
    # Check AI dependencies
    if not no_ai:
        deps = check_ai_dependencies()
        typer.echo("=== AI Dependencies ===")
        for name, available in deps.items():
            status = "✓" if available else "✗"
            typer.echo(f"  {status} {name}")
        typer.echo("")
    
    # Create classifier
    config.ensure_dirs()
    enable_ai = not no_ai
    
    try:
        classifier = ItemClassifier(
            db_path=config.db_path,
            clip_threshold=config.clip_threshold,
            text_threshold=config.text_threshold,
            fuzzy_threshold=config.fuzzy_threshold,
            enable_ai=enable_ai,
        )
    except ImportError as e:
        typer.echo(f"AI initialization failed: {e}")
        typer.echo("Falling back to fuzzy matching only...")
        classifier = ItemClassifier(
            db_path=config.db_path,
            fuzzy_threshold=config.fuzzy_threshold,
            enable_ai=False,
        )
    
    # Classify
    typer.echo(f"=== Classifying: {file_p.name} ===\n")
    result = classifier.classify(file_p, thumb_p)
    
    # Display result
    skip_status = "🚫 SKIP" if result.should_skip else "✓ KEEP"
    typer.echo(f"Decision: {skip_status}")
    typer.echo(f"Confidence: {result.confidence:.1%}")
    typer.echo(f"Category: {result.category or 'None'}")
    typer.echo(f"Reason: {result.reason}")
    typer.echo(f"Strategies: {', '.join(result.strategies_used)}")
    
    classifier.close()


@app.command("process-all")
def process_all(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be processed without actually converting",
    ),
    limit: int = typer.Option(
        0,
        "--limit", "-l",
        help="Maximum number of files to process (0 = all)",
    ),
    skip_ai: bool = typer.Option(
        False,
        "--skip-ai",
        help="Skip AI classification, use keyword matching only",
    ),
):
    """
    Process all existing files through the VRM pipeline.
    
    Scans data/raw and data/extracted directories, extracts archives,
    converts supported formats to VRM, and adds to database.
    
    Skips accessories, PMX files, and already-converted files.
    """
    from archive import is_skippable
    from converter import vrm_exists_for, get_converter_status
    
    # Check converter availability
    status = get_converter_status()
    if not status["blender"]["available"]:
        typer.echo("✗ Blender not found - required for conversion")
        typer.echo("  Install from https://www.blender.org/download/")
        raise typer.Exit(1)
    
    typer.echo(f"✓ Blender: {status['blender']['path']}")
    if skip_ai:
        typer.echo("⚠ AI classification disabled (--skip-ai)")
    typer.echo("")
    
    config.ensure_dirs()
    store = get_store()
    downloads = get_downloads_tracker()
    pipeline = VRMPipeline(store, downloads, config.extracted_dir)
    
    # Collect files to process
    convertible_extensions = {".fbx", ".obj", ".blend", ".glb", ".vrm"}
    archive_extensions = {".zip", ".rar", ".7z"}
    
    files_to_process: list[tuple[Path, str]] = []  # (path, category)
    
    # Scan raw directory for archives
    typer.echo(f"Scanning {config.raw_dir}...")
    for ext in archive_extensions:
        for file_path in config.raw_dir.rglob(f"*{ext}"):
            if file_path.is_file():
                files_to_process.append((file_path, "archive"))
    
    # Scan extracted directory for 3D files
    typer.echo(f"Scanning {config.extracted_dir}...")
    for ext in convertible_extensions:
        for file_path in config.extracted_dir.rglob(f"*{ext}"):
            if file_path.is_file():
                # Check if should skip (use AI unless --skip-ai)
                should_skip, reason = is_skippable(file_path, use_ai=not skip_ai)
                if should_skip:
                    continue
                # Check if VRM already exists
                if ext != ".vrm" and vrm_exists_for(file_path):
                    continue
                files_to_process.append((file_path, "model"))
    
    if not files_to_process:
        typer.echo("\nNo files to process.")
        store.close()
        downloads.close()
        return
    
    # Count by category
    archives = [f for f, c in files_to_process if c == "archive"]
    models = [f for f, c in files_to_process if c == "model"]
    
    typer.echo(f"\nFound {len(files_to_process)} files:")
    typer.echo(f"  Archives: {len(archives)}")
    typer.echo(f"  3D Models: {len(models)}")
    
    if limit > 0:
        files_to_process = files_to_process[:limit]
        typer.echo(f"\nProcessing first {limit} files...")
    
    if dry_run:
        typer.echo("\n(Dry run - showing files that would be processed)")
        for file_path, category in files_to_process[:20]:
            typer.echo(f"  [{category}] {file_path.name}")
        if len(files_to_process) > 20:
            typer.echo(f"  ... and {len(files_to_process) - 20} more")
        store.close()
        downloads.close()
        return
    
    typer.echo("\nProcessing...")
    processed = 0
    converted = 0
    skipped = 0
    failed = 0
    
    for file_path, category in files_to_process:
        # Extract source info from path
        try:
            if config.raw_dir in file_path.parents or file_path.parent == config.raw_dir:
                rel_path = file_path.relative_to(config.raw_dir)
            else:
                rel_path = file_path.relative_to(config.extracted_dir)
            parts = rel_path.parts
            source = parts[0] if len(parts) > 1 else "local"
            model_id = parts[1] if len(parts) > 2 else file_path.stem
        except ValueError:
            source = "local"
            model_id = file_path.stem
        
        if category == "archive":
            # Process archive through pipeline
            typer.echo(f"  📦 {file_path.name}")
            try:
                records = pipeline.process_download(
                    source=source,
                    model_id=model_id,
                    file_path=file_path,
                    name=file_path.stem,
                )
                if records:
                    converted += len(records)
                    typer.echo(f"     → {len(records)} VRM(s) created")
                else:
                    skipped += 1
                processed += 1
            except Exception as e:
                typer.echo(f"     ✗ Error: {e}")
                failed += 1
        else:
            # Convert single model file
            ext = file_path.suffix.lower()
            if ext == ".vrm":
                # Already VRM, just add to database
                typer.echo(f"  🎭 {file_path.name} (already VRM)")
                record = pipeline._create_model_record(
                    vrm_path=file_path,
                    source=source,
                    model_id=model_id,
                    name=file_path.stem,
                    artist="Unknown",
                    source_url="",
                    license_info=None,
                    thumbnail_path=None,
                    original_format="vrm",
                    timestamp=__import__("datetime").datetime.now().isoformat(),
                )
                if record:
                    converted += 1
                else:
                    skipped += 1
                processed += 1
            else:
                # Convert to VRM
                typer.echo(f"  🔄 {file_path.name}")
                result = pipeline.convert_file(file_path)
                if result.success and result.output_path:
                    record = pipeline._create_model_record(
                        vrm_path=result.output_path,
                        source=source,
                        model_id=model_id,
                        name=file_path.stem,
                        artist="Unknown",
                        source_url="",
                        license_info=None,
                        thumbnail_path=None,
                        original_format=result.original_format,
                        timestamp=__import__("datetime").datetime.now().isoformat(),
                    )
                    if record:
                        converted += 1
                        typer.echo(f"     → VRM created")
                    else:
                        skipped += 1
                    processed += 1
                else:
                    typer.echo(f"     ✗ {result.error or 'Conversion failed'}")
                    failed += 1
    
    store.close()
    downloads.close()
    
    typer.echo(f"\n=== Results ===")
    typer.echo(f"Processed: {processed}")
    typer.echo(f"VRMs created: {converted}")
    typer.echo(f"Skipped (existing): {skipped}")
    typer.echo(f"Failed: {failed}")


if __name__ == "__main__":
    app()
