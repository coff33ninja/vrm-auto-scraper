"""3D model format converter using Blender."""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Blender script for FBX/Blend to GLB conversion
BLENDER_CONVERT_SCRIPT = '''
import bpy
import sys
import os
import glob

# Get arguments after "--"
argv = sys.argv
argv = argv[argv.index("--") + 1:]
input_file = argv[0]
output_file = argv[1]

print(f"Input: {input_file}")
print(f"Output: {output_file}")

# Get the directory of the input file for texture searching
input_dir = os.path.dirname(input_file)
parent_dir = os.path.dirname(input_dir)

# Build list of texture search directories
texture_dirs = [input_dir]
# Check common texture folder names
for tex_folder in ["Textures", "textures", "Texture", "texture", "tex", "maps", "Materials"]:
    tex_path = os.path.join(input_dir, tex_folder)
    if os.path.isdir(tex_path):
        texture_dirs.append(tex_path)
    # Also check parent directory
    tex_path = os.path.join(parent_dir, tex_folder)
    if os.path.isdir(tex_path):
        texture_dirs.append(tex_path)

print(f"Texture search dirs: {texture_dirs}")

# Clear default scene
bpy.ops.wm.read_factory_settings(use_empty=True)

# Import based on file type
ext = input_file.lower().split(".")[-1]
print(f"File type: {ext}")

def find_texture(name):
    """Search for texture file in known directories."""
    base_name = os.path.splitext(os.path.basename(name))[0]
    for tex_dir in texture_dirs:
        for ext in [".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff"]:
            # Try exact name
            path = os.path.join(tex_dir, base_name + ext)
            if os.path.exists(path):
                return path
            # Try case-insensitive
            for f in glob.glob(os.path.join(tex_dir, "*" + ext)):
                if os.path.basename(f).lower() == (base_name + ext).lower():
                    return f
    return None

try:
    if ext == "fbx":
        # Import FBX with automatic texture search
        bpy.ops.import_scene.fbx(
            filepath=input_file,
            use_image_search=True,  # Search for textures
            use_alpha_decals=False,
            decal_offset=0.0,
        )
    elif ext == "obj":
        bpy.ops.wm.obj_import(filepath=input_file)
    elif ext == "blend":
        # For blend files, append all objects, materials, and textures
        with bpy.data.libraries.load(input_file, link=False) as (data_from, data_to):
            data_to.objects = data_from.objects
            data_to.materials = data_from.materials
            data_to.images = data_from.images
        for obj in data_to.objects:
            if obj is not None:
                bpy.context.collection.objects.link(obj)
    else:
        print(f"Unsupported format: {ext}")
        sys.exit(1)
    
    print(f"Import successful. Objects: {len(bpy.data.objects)}")
    print(f"Materials: {len(bpy.data.materials)}")
    print(f"Images referenced: {len(bpy.data.images)}")
    
    # Build a map of texture names to file paths
    texture_map = {}
    for tex_dir in texture_dirs:
        if os.path.isdir(tex_dir):
            for f in os.listdir(tex_dir):
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.tga', '.bmp')):
                    base = os.path.splitext(f)[0].lower()
                    texture_map[base] = os.path.join(tex_dir, f)
    
    print(f"Found {len(texture_map)} texture files in search dirs")
    
    # Load all images properly
    loaded_images = {}
    for img in list(bpy.data.images):
        if not img.filepath:
            continue
        
        # Get the base name without extension
        img_basename = os.path.splitext(os.path.basename(img.filepath))[0].lower()
        
        # Try to find the actual file
        actual_path = None
        abs_path = bpy.path.abspath(img.filepath)
        
        if os.path.exists(abs_path):
            actual_path = abs_path
        elif img_basename in texture_map:
            actual_path = texture_map[img_basename]
        else:
            # Try partial match
            for key, path in texture_map.items():
                if img_basename in key or key in img_basename:
                    actual_path = path
                    break
        
        if actual_path and os.path.exists(actual_path):
            # Load the image properly
            try:
                new_img = bpy.data.images.load(actual_path, check_existing=True)
                new_img.pack()
                loaded_images[img.name] = new_img
                print(f"  Loaded: {img.name} -> {os.path.basename(actual_path)}")
            except Exception as e:
                print(f"  Failed to load {img.name}: {e}")
        else:
            print(f"  Not found: {img.name} ({img.filepath})")
    
    print(f"Successfully loaded {len(loaded_images)} images")
    
    # Convert materials to use Principled BSDF for better glTF export
    # This helps with game models that use custom shaders
    converted_mats = 0
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            mat.use_nodes = True
            
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        
        # Find the output node or create one
        output_node = None
        for node in nodes:
            if node.type == 'OUTPUT_MATERIAL':
                output_node = node
                break
        
        if not output_node:
            output_node = nodes.new('ShaderNodeOutputMaterial')
            output_node.location = (400, 0)
        
        # Collect all image texture nodes and update their images
        tex_nodes = []
        for node in nodes:
            if node.type == 'TEX_IMAGE' and node.image:
                # Replace with loaded image if available
                if node.image.name in loaded_images:
                    node.image = loaded_images[node.image.name]
                tex_nodes.append(node)
        
        # Get material base name for texture matching
        mat_base = mat.name.lower().replace('mat_', '').replace('_', '')
        
        # Categorize textures by type based on naming conventions
        diffuse_tex = None
        normal_tex = None
        metallic_tex = None
        alpha_tex = None
        roughness_tex = None
        
        for node in tex_nodes:
            if not node.image:
                continue
            name_lower = node.image.name.lower()
            
            # Diffuse/Base Color (_D, _diffuse, _color, _albedo, _basecolor)
            if diffuse_tex is None:
                if '_d.' in name_lower or '_d_' in name_lower or name_lower.endswith('_d.png') or name_lower.endswith('_d.jpg'):
                    diffuse_tex = node
                elif any(x in name_lower for x in ['diffuse', 'color', 'albedo', 'basecolor', 'base_color']):
                    diffuse_tex = node
            
            # Normal map (_N, _normal, _nrm)
            if normal_tex is None:
                if '_n.' in name_lower or '_n_' in name_lower or name_lower.endswith('_n.png') or name_lower.endswith('_n.jpg'):
                    normal_tex = node
                elif any(x in name_lower for x in ['normal', 'nrm', 'norm']):
                    normal_tex = node
            
            # Metallic (_M, _metallic, _metal)
            if metallic_tex is None:
                if '_m.' in name_lower or '_m_' in name_lower or name_lower.endswith('_m.png') or name_lower.endswith('_m.jpg'):
                    metallic_tex = node
                elif any(x in name_lower for x in ['metallic', 'metal', 'metalness']):
                    metallic_tex = node
            
            # Alpha/Opacity (_A, _alpha, _opacity)
            if alpha_tex is None:
                if '_a.' in name_lower or '_a_' in name_lower or name_lower.endswith('_a.png') or name_lower.endswith('_a.jpg'):
                    alpha_tex = node
                elif any(x in name_lower for x in ['alpha', 'opacity', 'transparent']):
                    alpha_tex = node
            
            # Roughness (_R, _roughness, _rough)
            if roughness_tex is None:
                if '_r.' in name_lower or '_r_' in name_lower or name_lower.endswith('_r.png') or name_lower.endswith('_r.jpg'):
                    roughness_tex = node
                elif any(x in name_lower for x in ['roughness', 'rough']):
                    roughness_tex = node
        
        # If no diffuse found by naming, use the first texture
        if diffuse_tex is None and tex_nodes:
            diffuse_tex = tex_nodes[0]
        
        # Skip materials with no textures
        if not diffuse_tex:
            continue
        
        # Clear all existing links
        links.clear()
        
        # Create or find Principled BSDF
        principled = None
        for node in nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled = node
                break
        
        if principled is None:
            principled = nodes.new('ShaderNodeBsdfPrincipled')
            principled.location = (100, 0)
        
        # Position texture nodes
        y_offset = 300
        
        # Connect diffuse texture to Base Color
        diffuse_tex.location = (-400, y_offset)
        links.new(diffuse_tex.outputs['Color'], principled.inputs['Base Color'])
        y_offset -= 300
        
        # Handle alpha - check diffuse texture alpha channel or separate alpha texture
        has_alpha = False
        if alpha_tex and alpha_tex.image:
            alpha_tex.location = (-400, y_offset)
            links.new(alpha_tex.outputs['Color'], principled.inputs['Alpha'])
            has_alpha = True
            y_offset -= 300
        elif diffuse_tex.image and diffuse_tex.image.channels == 4:
            links.new(diffuse_tex.outputs['Alpha'], principled.inputs['Alpha'])
            has_alpha = True
        
        if has_alpha:
            mat.blend_method = 'BLEND'
        
        # Connect normal map
        if normal_tex and normal_tex.image:
            normal_tex.location = (-400, y_offset)
            normal_tex.image.colorspace_settings.name = 'Non-Color'
            normal_map = nodes.new('ShaderNodeNormalMap')
            normal_map.location = (-100, y_offset)
            links.new(normal_tex.outputs['Color'], normal_map.inputs['Color'])
            links.new(normal_map.outputs['Normal'], principled.inputs['Normal'])
            y_offset -= 300
        
        # Connect metallic map
        if metallic_tex and metallic_tex.image:
            metallic_tex.location = (-400, y_offset)
            metallic_tex.image.colorspace_settings.name = 'Non-Color'
            links.new(metallic_tex.outputs['Color'], principled.inputs['Metallic'])
            y_offset -= 300
        
        # Connect roughness map
        if roughness_tex and roughness_tex.image:
            roughness_tex.location = (-400, y_offset)
            roughness_tex.image.colorspace_settings.name = 'Non-Color'
            links.new(roughness_tex.outputs['Color'], principled.inputs['Roughness'])
            y_offset -= 300
        
        # Connect Principled BSDF to output
        links.new(principled.outputs['BSDF'], output_node.inputs['Surface'])
        
        converted_mats += 1
        tex_info = f"D:{diffuse_tex.image.name if diffuse_tex else 'none'}"
        if normal_tex: tex_info += f" N:{normal_tex.image.name}"
        if metallic_tex: tex_info += f" M:{metallic_tex.image.name}"
        if alpha_tex: tex_info += f" A:{alpha_tex.image.name}"
        print(f"  Rebuilt material: {mat.name} [{tex_info}]")
    
    print(f"Converted {converted_mats} materials to Principled BSDF")
    
    # Export to GLB with textures embedded
    # Blender 5.0 changed the GLTF export API
    bpy.ops.export_scene.gltf(
        filepath=output_file,
        export_format="GLB",
        export_image_format="AUTO",  # Auto-detect best format
        export_materials="EXPORT",   # Export materials
        export_texcoords=True,
        export_normals=True,
        export_animations=True,
        export_skins=True,
        export_morph=True,
    )
    
    print(f"Export complete: {output_file}")
    
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
'''


def find_blender() -> str | None:
    """Find Blender executable on the system."""
    # Check if blender is in PATH
    if shutil.which("blender"):
        return "blender"
    
    # Common Windows installation paths
    windows_paths = [
        r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender\blender.exe",
    ]
    
    for path in windows_paths:
        if Path(path).exists():
            return path
    
    return None


def find_fbx2gltf() -> str | None:
    """Find FBX2glTF executable on the system."""
    if shutil.which("FBX2glTF"):
        return "FBX2glTF"
    if shutil.which("fbx2gltf"):
        return "fbx2gltf"
    return None


BLENDER_PATH = find_blender()
FBX2GLTF_PATH = find_fbx2gltf()


def convert_with_blender(input_path: Path, output_path: Path) -> bool:
    """
    Convert a 3D model to GLB using Blender.
    
    Supports: FBX, OBJ, Blend files
    """
    if not BLENDER_PATH:
        logger.error("Blender not found. Install Blender from https://www.blender.org/download/")
        return False
    
    # Write the conversion script to a temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(BLENDER_CONVERT_SCRIPT)
        script_path = f.name
    
    try:
        # Run Blender in background mode
        cmd = [
            BLENDER_PATH,
            "--background",
            "--python", script_path,
            "--",
            str(input_path.absolute()),
            str(output_path.absolute()),
        ]
        
        logger.info(f"Converting {input_path.name} with Blender...")
        logger.debug(f"Command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        # Always show Blender output for debugging
        if result.stdout:
            for line in result.stdout.split('\n'):
                if line.strip() and not line.startswith('Blender'):
                    logger.info(f"  Blender: {line.strip()}")
        
        if result.returncode != 0:
            logger.error(f"Blender conversion failed (exit code {result.returncode})")
            logger.error(f"stderr: {result.stderr}")
            return False
        
        if output_path.exists():
            logger.info(f"Successfully converted to {output_path.name}")
            return True
        else:
            logger.error(f"Output file not created. Blender output:\n{result.stdout[-2000:]}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("Blender conversion timed out")
        return False
    except Exception as e:
        logger.error(f"Conversion error: {e}")
        return False
    finally:
        # Clean up temp script
        Path(script_path).unlink(missing_ok=True)


def convert_with_fbx2gltf(input_path: Path, output_path: Path) -> bool:
    """
    Convert FBX to GLB using FBX2glTF tool.
    
    Faster than Blender but only supports FBX files.
    """
    if not FBX2GLTF_PATH:
        logger.warning("FBX2glTF not found, falling back to Blender")
        return False
    
    try:
        # FBX2glTF adds .glb extension automatically, so we need to handle that
        output_base = output_path.with_suffix("")
        
        cmd = [
            FBX2GLTF_PATH,
            "--binary",
            "--input", str(input_path),
            "--output", str(output_base),
        ]
        
        logger.info(f"Converting {input_path.name} with FBX2glTF...")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        
        if result.returncode != 0:
            logger.error(f"FBX2glTF conversion failed: {result.stderr}")
            return False
        
        # Check if output was created (FBX2glTF adds _out.glb suffix)
        possible_outputs = [
            output_path,
            output_base.with_suffix(".glb"),
            Path(str(output_base) + "_out.glb"),
        ]
        
        for out in possible_outputs:
            if out.exists():
                if out != output_path:
                    out.rename(output_path)
                logger.info(f"Successfully converted to {output_path.name}")
                return True
        
        logger.error("Output file not created")
        return False
        
    except subprocess.TimeoutExpired:
        logger.error("FBX2glTF conversion timed out")
        return False
    except Exception as e:
        logger.error(f"Conversion error: {e}")
        return False


def convert_to_glb(input_path: Path, output_path: Path | None = None) -> Path | None:
    """
    Convert a 3D model file to GLB format.
    
    Supports: FBX, OBJ, Blend files
    
    Args:
        input_path: Path to input file
        output_path: Optional output path (defaults to same name with .glb extension)
        
    Returns:
        Path to converted file, or None if conversion failed
    """
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        return None
    
    ext = input_path.suffix.lower()
    supported = {".fbx", ".obj", ".blend"}
    
    if ext not in supported:
        logger.error(f"Unsupported format: {ext}. Supported: {supported}")
        return None
    
    if output_path is None:
        output_path = input_path.with_suffix(".glb")
    
    # Try FBX2glTF first for FBX files (faster)
    if ext == ".fbx" and FBX2GLTF_PATH:
        if convert_with_fbx2gltf(input_path, output_path):
            return output_path
    
    # Fall back to Blender
    if convert_with_blender(input_path, output_path):
        return output_path
    
    return None


def get_converter_status() -> dict:
    """Get status of available converters."""
    return {
        "blender": {
            "available": BLENDER_PATH is not None,
            "path": BLENDER_PATH,
            "formats": ["fbx", "obj", "blend"] if BLENDER_PATH else [],
        },
        "fbx2gltf": {
            "available": FBX2GLTF_PATH is not None,
            "path": FBX2GLTF_PATH,
            "formats": ["fbx"] if FBX2GLTF_PATH else [],
        },
    }
