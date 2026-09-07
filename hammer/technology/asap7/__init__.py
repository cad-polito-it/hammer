#  asap7 plugin for Hammer.
#
#  See LICENSE for licence details.

import sys
import re
import os
import shutil
import glob
import subprocess
import textwrap
from types import new_class
from typing import NamedTuple, List, Optional, Tuple, Dict, Set, Any

from hammer.tech import HammerTechnology
from hammer.vlsi import HammerTool, HammerPlaceAndRouteTool, HammerSynthesisTool, HammerDRCTool, MentorCalibreTool, TCLTool, HammerToolHookAction

class ASAP7Tech(HammerTechnology):
    """
    Override the HammerTechnology used in `hammer_tech.py`
    This class is loaded by function `load_from_json`, and will pass the `try` in `importlib`.
    """
    def post_install_script(self) -> None:
        try:
            # Prioritize gdstk
            self.gds_tool = __import__('gdstk')
        except ImportError:
            self.logger.info("gdstk not found, falling back to gdspy...")
            try:
                self.gds_tool = __import__('gdspy')
                assert('1.4' in self.gds_tool.__version__)
            except (ImportError, AssertionError):
                self.logger.error("Check your gdspy v1.4 installation! Unable to hack ASAP7 PDK.")
                shutil.rmtree(self.cache_dir)
                sys.exit()
        self.generate_multi_vt_gds()
        # TODO: (franout) verify if it works for cadence flow
        self.fix_icg_libs()
        self.fix_primitives()

    def generate_multi_vt_gds(self) -> None:
        """
        PDK GDS only contains RVT cells.
        This patch will generate the other 3(LVT, SLVT, SRAM) VT GDS files.
        """
        try:
            os.makedirs(os.path.join(self.cache_dir, "GDS"))
        except:
            self.logger.info("Multi-VT GDS's already created")
            return None

        try:
            self.logger.info("Generating GDS for Multi-VT cells using {}...".format(self.gds_tool.__name__))

            stdcell_dir = self.get_setting("technology.asap7.stdcell_install_dir")
            orig_gds = os.path.join(stdcell_dir, "GDS/asap7sc7p5t_27_R_201211.gds")
            cell_list: Optional[List[str]] = None

            if self.gds_tool.__name__ == 'gdstk':
                # load original GDS
                asap7_original_gds = self.gds_tool.read_gds(infile=orig_gds)
                original_cells = asap7_original_gds.cells
                cell_list = list(map(lambda c: c.name, original_cells))
                # required libs
                multi_libs = {
                    "L": {
                        "lib": self.gds_tool.Library(),
                        "mvt_layer": 98
                        },
                    "SL": {
                        "lib": self.gds_tool.Library(),
                        "mvt_layer": 97
                        },
                    "SRAM": {
                        "lib": self.gds_tool.Library(),
                        "mvt_layer": 110
                        },
                }
                # create new libs
                for vt, multi_lib in multi_libs.items():
                    multi_lib['lib'].name = asap7_original_gds.name.replace('R', vt)

                for cell in original_cells:
                    # extract polygon from layer 100(the boundary for cell)
                    boundary_polygon = next(filter(lambda p: p.layer==100, cell.polygons))
                    for vt, multi_lib in multi_libs.items():
                        new_cell_name = cell.name.rstrip('R') + vt
                        cell_list.append(new_cell_name)
                        mvt_layer = multi_lib['mvt_layer']
                        # copy boundary_polygon to mvt_layer to mark the this cell is a mvt cell.
                        mvt_polygon = self.gds_tool.Polygon(boundary_polygon.points, multi_lib['mvt_layer'], 0)
                        mvt_cell = cell.copy(name=new_cell_name, deep_copy=True).add(mvt_polygon)
                        # add mvt_cell to corresponding multi_lib
                        multi_lib['lib'].add(mvt_cell)

                for vt, multi_lib in multi_libs.items():
                    # write multi_lib
                    new_gds = os.path.basename(orig_gds).replace('R', vt)
                    multi_lib['lib'].write_gds(os.path.join(self.cache_dir, 'GDS', new_gds))

            elif self.gds_tool.__name__ == 'gdspy':
                # load original GDS
                asap7_original_gds = self.gds_tool.GdsLibrary().read_gds(infile=orig_gds, units='import')
                original_cells = asap7_original_gds.cell_dict
                cell_list = list(map(lambda c: c.name, original_cells.values()))
                # required libs
                multi_libs = {
                    "L": {
                        "lib": self.gds_tool.GdsLibrary(),
                        "mvt_layer": 98
                        },
                    "SL": {
                        "lib": self.gds_tool.GdsLibrary(),
                        "mvt_layer": 97
                        },
                    "SRAM": {
                        "lib": self.gds_tool.GdsLibrary(),
                        "mvt_layer": 110
                        },
                }
                # create new libs
                for vt, multi_lib in multi_libs.items():
                    multi_lib['lib'].name = asap7_original_gds.name.replace('R', vt)

                for cell in original_cells.values():
                    poly_dict = cell.get_polygons(by_spec=True)
                    # extract polygon from layer 100(the boundary for cell)
                    boundary_polygon = poly_dict[(100, 0)]
                    for vt, multi_lib in multi_libs.items():
                        new_cell_name = cell.name.rstrip('R') + vt
                        cell_list.append(new_cell_name)
                        mvt_layer = multi_lib['mvt_layer']
                        # copy boundary_polygon to mvt_layer to mark the this cell is a mvt cell.
                        mvt_polygon = self.gds_tool.PolygonSet(boundary_polygon, multi_lib['mvt_layer'], 0)
                        mvt_cell = cell.copy(name=new_cell_name, exclude_from_current=True, deep_copy=True).add(mvt_polygon)
                        # add mvt_cell to corresponding multi_lib
                        multi_lib['lib'].add(mvt_cell)

                for vt, multi_lib in multi_libs.items():
                    # write multi_lib
                    new_gds = os.path.basename(orig_gds).replace('R', vt)
                    multi_lib['lib'].write_gds(os.path.join(self.cache_dir, 'GDS', new_gds))

            # Write out cell list for scaling script
            with open(os.path.join(self.cache_dir, 'stdcells.txt'), 'w') as f:
                assert cell_list
                f.writelines('{}\n'.format(cell) for cell in cell_list)

        except:
            os.rmdir(os.path.join(self.cache_dir, "GDS"))
            self.logger.error("GDS patching failed! Check your gdstk, gdspy, and/or ASAP7 PDK installation.")
            sys.exit()

    def fix_scan_cells(self, nlib_path: str) -> None:
        """Fixing already present scan cells"""
        self.logger.info("Fixing Scan cells...")
        test_cells = "\\n".join(['        test_cell () {',
		'           pin(D) {',
		'                direction : input;',
		'           }',
		'           pin(CLK) {',
		'               direction : input;',
		'           }',
		'           pin(SI) {',
		'                direction : input;',
		'                signal_type : test_scan_in;',
		'           }',
		'           pin(SE) {',
		'                direction : input;',
		'                signal_type : test_scan_enable;',
		'           }',
        '           ff (IQN,IQNN) {',
		'                next_state : "!D";',
		'                clocked_on         	: "CLK";',
		'           }	     ',
		'           pin(QN) {',
		'                direction : output;',
		'                function : "IQN";',
		'                signal_type : test_scan_out;',
		'          }',
		'        }',])
        test_cells_inverted = "\\n".join(['        test_cell () {',
		'           pin(D) {',
		'                direction : input;',
		'           }',
		'           pin(CLK) {',
		'               direction : input;',
		'           }',
		'           pin(SI) {',
		'                direction : input;',
		'                signal_type : test_scan_in;',
		'           }',
		'           pin(SE) {',
		'                direction : input;',
		'                signal_type : test_scan_enable;',
		'           }',
		'           ff (IQN,IQNN) {',
		'                next_state : "!D";',
		'                clocked_on         	: "!CLK";',
		'           }	     ',
		'           pin(QN) {',
		'                direction : output;'
		'                function : "IQN";',
		'                signal_type : test_scan_out;',
		'          }',
		'        }',])

        # Add nextstate
        subprocess.call(["sed -i '/cell (SDF.*/,/^}}/ {{ /pin (SE) {{/a \\      nextstate_type: scan_enable; \n}}' {nlib}".format(nlib=nlib_path)], shell=True)
        subprocess.call(["sed -i '/cell (SDF.*/,/^}}/ {{ /pin (D) {{/a \\      nextstate_type: data; \n}}' {nlib}".format(nlib=nlib_path)], shell=True)
        subprocess.call(["sed -i '/cell (SDF.*/,/^}}/ {{ /pin (SI) {{/a \\      nextstate_type: scan_in; \n}}' {nlib}".format(nlib=nlib_path)], shell=True)
        subprocess.call(["sed -i '/cell (SDFHx*/a {test_cell_group}' {nlib}".format(test_cell_group=test_cells, nlib=nlib_path)], shell=True)
        subprocess.call(["sed -i '/cell (SDFLx*/a {test_cell_group}' {nlib}".format(test_cell_group=test_cells_inverted, nlib=nlib_path)], shell=True)    


    def delete_pin_in_specific_cell(self, input_file, cell_regex_str, output_file=None):
        """
        Removes the pin (IQ) block ONLY within cells matching cell_regex_str.
        """
        if output_file is None:
            output_file = input_file

        cell_pattern = re.compile(cell_regex_str)
        pin_start_pattern = re.compile(r'pin\s*\(IQ\)\s*\{')
        
        # Matches a closing brace that sits alone or starts a line
        # (Typical for cell and pin boundaries in Liberty files)
        closing_brace_pattern = re.compile(r'^\s*\}')

        remaining_lines = []
        
        in_target_cell = False
        delete_mode = False
        brace_count = 0  # To track exactly when we leave the target cell

        with open(input_file, 'r') as f:
            for line in f:
                
                # --- PHASE 1: Find the right cell ---
                if not in_target_cell:
                    if cell_pattern.search(line):
                        in_target_cell = True
                        brace_count = line.count('{') - line.count('}')
                    remaining_lines.append(line)
                    continue

                # --- PHASE 2: Inside the target cell ---
                # Track braces to know when this specific cell completely ends
                brace_count += line.count('{') - line.count('}')

                if not delete_mode:
                    # Look for the pin block start inside our target cell
                    if pin_start_pattern.search(line):
                        delete_mode = True
                        continue  # Skip this line (start deleting)
                    
                    remaining_lines.append(line)
                else:
                    # We are deleting. Look for the pin's closing brace
                    if closing_brace_pattern.match(line):
                        delete_mode = False  # Stop deleting after this line
                    continue  # Skip all lines while delete_mode is True

                # If brace_count hits 0, we have exited the targeted cell block
                if brace_count <= 0:
                    in_target_cell = False

        # Write the modified contents back
        with open(output_file, 'w') as f:
            f.writelines(remaining_lines)

    def insert_text_in_file(self, input_file_path, output_file_path, cell_pattern, insert_after_pattern, insert_text):
        """
        Reads a file line-by-line and inserts text after a pattern inside a cell block.
        Safely handles cases where input_file_path and output_file_path are identical.
        """
        if not os.path.exists(input_file_path):
            raise FileNotFoundError(f"Input file '{input_file_path}' not found.")

        # Check if we are modifying the file in-place
        is_inplace = os.path.abspath(input_file_path) == os.path.abspath(output_file_path)
        
        # If in-place, write to a temporary file first so we don't erase our source data
        actual_output_path = output_file_path + ".tmp" if is_inplace else output_file_path

        inside_target_cell = False
        inserted_any = False

        try:
            with open(input_file_path, "r") as in_file, open(actual_output_path, "w") as out_file:
                for line in in_file:
                    if re.search(cell_pattern, line):
                        inside_target_cell = True
                    
                    out_file.write(line)
                    
                    if inside_target_cell and re.search(insert_after_pattern, line):
                        out_file.write(insert_text + "\n")
                        inserted_any = True
                        
                    if inside_target_cell and line.strip() == "}":
                        inside_target_cell = False

            # If it was an in-place operation, cleanly swap the temp file with the original
            if is_inplace:
                os.replace(actual_output_path, output_file_path)

            return inserted_any

        except Exception as e:
            if is_inplace and os.path.exists(actual_output_path):
                os.remove(actual_output_path)
            raise e
    
    
    def fix_primitives(self) -> None:
            """
            Finds Verilog files in the ASAP7 tech library, copies/extracts them 
            to the cache directory, and patches the broken 'altos_dff_sr_err' sequential UDP.
            """
            verilog_cache_dir = os.path.join(self.cache_dir, "Verilog")
            os.makedirs(verilog_cache_dir,exist_ok=True)
            
            try:
                self.logger.info("Fixing broken Verilog UDP primitives...")

                # Define the exact corrected primitive block
                corrected_primitive = """
/* Patched primitive by HAMMER */
primitive altos_dff_sr_err (q, clk, d, s, r);
output q;
reg q;
input clk, d, s, r;

    table
        //  clk     d     s     r     : q : q_next
        //-----------------------------------------
        // Asynchronous/Stable level behavior (keeps old state)
            ?       ?     ?     ?     : ? : - ; 

        // Clock falling edge transitions (keeps old state)
           (10)     ?     ?     ?     : ? : - ;
           (x0)     ?     ?     ?     : ? : - ;
           (1x)     ?     ?     ?     : ? : - ;

        // Asynchronous pin edge transitions (keeps old state)
            ?       ?     ?    (01)   : ? : - ;
            ?       ?    (01)   ?     : ? : - ;
            ?       ?     ?    (x1)   : ? : - ;
            ?       ?    (x1)   ?     : ? : - ;

        // Valid Clock rising edge transitions (Data -> Output)
           (01)     0     0     0     : ? : 0 ;
           (01)     1     0     0     : ? : 1 ;
           (0x)     0     0     0     : ? : 0 ;
           (0x)     1     0     0     : ? : 1 ;

        // Handles Unknown/X transitions safely
           (01)     x     0     0     : ? : x ;
           (01)     ?     x     ?     : ? : x ;
           (01)     ?     ?     x     : ? : x ;
        endtable
endprimitive"""

                # Locate the source Verilog directory from the ASAP7 installation settings
                tech_verilog_dir = os.path.join(self.get_setting("technology.asap7.stdcell_install_dir"), "Verilog")
                old_vfiles = glob.glob(os.path.join(tech_verilog_dir, "*"))
                
                if not old_vfiles:
                    raise FileNotFoundError("No Verilog files found in the source tech directory.")

                # Map source file paths to the new cache target paths
                new_vfiles = list(map(lambda v: os.path.join(verilog_cache_dir, os.path.basename(v)), old_vfiles))

                # Regular expression to match the malformed primitive
                primitive_pattern = re.compile(r"primitive\s+altos_dff_sr_err\b.*?endprimitive", re.DOTALL)
                
                # Map by filename instead of absolute source paths
                verilog_file_map = {}
                
                # 2. Iterate and patch each file
                for ovfile, nvfile in zip(old_vfiles, new_vfiles):
                    filename = os.path.basename(ovfile)
                    
                    # Copy into cache dir
                    subprocess.call([f"cp {ovfile} {nvfile}"], shell=True)

                    # Read the code
                    with open(nvfile, "r") as f:
                        content = f.read()
                    
                    # Apply the regex patch if the target primitive exists inside the file
                    if primitive_pattern.search(content):
                        patched_content = primitive_pattern.sub(corrected_primitive, content)
                        
                        # Write back the patched code
                        with open(nvfile, "w") as f:
                            f.write(patched_content)
                        self.logger.info(f"Patched primitive in: {filename}")
                    
                    # Always track the cached file path indexed by its base filename
                    verilog_file_map[filename] = nvfile

                self.logger.info("Successfully fixed all Verilog primitives!")
                new_libraries = []

                for lib in self.config.libraries:
                    update_dict = {}
                    
                    # 2. Handle Verilog Primitive updates
                    if hasattr(lib, 'verilog_sim') and lib.verilog_sim is not None:
                        # Extract the filename from the hammer configuration path string
                        v_basename = os.path.basename(lib.verilog_sim)
                        
                        # Match against our base filename map
                        if v_basename in verilog_file_map:
                            update_dict['verilog_sim'] = verilog_file_map[v_basename]
        
                    # 3. Apply updates to the database configuration if changes were made
                    if update_dict:
                        updated_lib = lib.copy(update=update_dict)
                        new_libraries.append(updated_lib)
                    else:
                        new_libraries.append(lib)

                # 4. Commit the new library definitions back to the technology config environment
                self.config.libraries = new_libraries

            except Exception as e:
                if os.path.exists(verilog_cache_dir):
                    try:
                        os.rmdir(verilog_cache_dir)
                    except OSError:
                        pass
                self.logger.error(f"Failed to fix Verilog primitives: {str(e)}")
                sys.exit(1)
            
    def fix_icg_libs(self) -> None:
        """
        ICG cells are missing statetable.
        """
        try:
            os.makedirs(os.path.join(self.cache_dir, "LIB/NLDM"))
        except:
            self.logger.info("ICG LIBs already fixed")
            return None

        try:
            self.logger.info("Fixing ICG LIBs...")

            latch_function = "\n".join([
            ' \t\t latch ("IQ", "IQN") {',
            '\t\t data_in : "ENA | SE"; ',
            '\t\t enable  : "!CLK"; ',
            '\t\t }'])

            lib_dir = os.path.join(self.get_setting("technology.asap7.stdcell_install_dir"), "LIB/NLDM")
            old_libs = glob.glob(os.path.join(lib_dir, "*"))
            new_libs = list(map(lambda l: os.path.join(self.cache_dir, "LIB/NLDM", os.path.basename(l)), old_libs))

            for olib, nlib in zip(old_libs, new_libs):
                # Use gzip and sed directly rather than gzip python module
                # Add the function for latch type
                nlib = nlib.replace(".7z","").replace(".gz","")
                subprocess.call(["7z x {olib} -so > {nlib}".format(olib=olib, nlib=nlib)],shell=True)
                # Add latch function
                self.insert_text_in_file( input_file_path=nlib,
                output_file_path=nlib,
                cell_pattern=r'cell\s*\(ICGx.*L\)',
                insert_after_pattern=r'clock_gating_integrated_cell',
                insert_text=latch_function
                    )
            
                self.insert_text_in_file( input_file_path=nlib,
                output_file_path=nlib,
                cell_pattern=r'cell\s*\(ICGx.*R\)',
                insert_after_pattern=r'clock_gating_integrated_cell',
                insert_text=latch_function
                    )
                
                # Remove pin IQ 
                self.delete_pin_in_specific_cell(nlib, r"cell\s*\(ICGx.*\)")
                # Add test cell definition 
                self.fix_scan_cells(nlib)
        except:
            os.rmdir(os.path.join(self.cache_dir, "LIB/NLDM"))
            os.rmdir(os.path.join(self.cache_dir, "LIB"))
            self.logger.error("Failed to fix ICG LIBs. Check your ASAP7 installation!")
            sys.exit()

    def get_tech_par_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        hooks = {"innovus": [
            HammerTool.make_post_persistent_hook("init_design", asap7_innovus_settings),
            HammerTool.make_post_insertion_hook("floorplan_design", asap7_update_floorplan),
            HammerTool.make_post_insertion_hook("write_design", asap7_scale_final_gds)
            ]}
        return hooks.get(tool_name, [])

    def get_tech_drc_hooks(self, tool_name: str) -> List[HammerToolHookAction]:
        hooks = {"calibre": [
            HammerTool.make_replacement_hook("generate_drc_run_file", asap7_generate_drc_run_file)
            ]}
        return hooks.get(tool_name, [])

    def get_tech_syn_hooks(self, tool_name:str) ->List[HammerToolHookAction]:
        hooks = {"dc": [
            HammerTool.make_persistent_hook(asap7_generate_db_files)
            ]}
        return hooks.get(tool_name, [])

def asap7_generate_db_files(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerSynthesisTool)
    library_file = {}
    convert_tcl_file = ht.script_dir + "/fromLib2db.tcl"
    convert_tcl = ""

    liberty_converted = False
    ## Get liberty files 
    for liberty in ht.timing_liberty:
        if "SRAM" not in liberty:
            lib_name = os.path.splitext(os.path.basename(liberty))[0]
            db = os.path.join( os.path.dirname(liberty) ,lib_name + ".db")
            # Skip if they already exists
            if os.path.exists(db):
                ht.logger.info(f"Liberty files ({lib_name}) already converted")
                liberty_converted = True 
            ## Update the tech json with DB files
            library_file[liberty] = db
            convert_tcl += f"read_lib {liberty}\n write_lib -f db -output {db} {lib_name}\n\n"
    
    new_libraries = []

    for lib in ht.technology.config.libraries:
        # Check if this library has the liberty file set but lacks the library file
        if lib.nldm_liberty_file is not None and "SRAM" not in lib.nldm_liberty_file:
            lib_path = lib.nldm_liberty_file.replace("cache", ht.technology.cache_dir)
            updated_lib = lib.copy(update={'nldm_library_file': library_file[lib_path]})
            new_libraries.append(updated_lib)
        else:
            new_libraries.append(lib)
    
    convert_tcl += "quit"

    with open(convert_tcl_file, "w") as f:
        f.write(convert_tcl)

    # Re-assign the updated list back to the technology config
    ht.technology.config.libraries = new_libraries
    ## Generate DB files 
    lc_bin = os.path.basename(ht.get_setting("synthesis.library_compiler.lc_bin"))
    # Let dump in the synthesis output log the library conversion
    args = [
        lc_bin,
        "-no_log",
        "-f", convert_tcl_file
    ]
    if not(liberty_converted):
        ht.run_executable(args = args ,cwd=ht.run_dir)
    return True

def asap7_innovus_settings(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "Innovus settings only for par"
    assert isinstance(ht, TCLTool), "innovus settings can only run on TCL tools"
    """Settings that may need to be reapplied at every tool invocation
    Note that the particular routing layer settings here will persist in Innovus;
    this hook only serves as an example of what commands may need to persist."""
    ht.append('''
# Ignore 1e+31 removal arcs for ASYNC DFF cells
set_db timing_analysis_async_checks no_async

# Via preferences for stripes
set_db generate_special_via_rule_preference { M7_M6widePWR1p152 M6_M5widePWR1p152 M5_M4widePWR0p864 M4_M3widePWR0p864 M3_M2widePWR0p936 }

# Prevent extending M1 pins in cells
set_db route_design_with_via_in_pin true
    ''')
    return True

def asap7_update_floorplan(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "asap7_update_floorplan can only run on par"
    assert isinstance(ht, TCLTool), "asap7_update_floorplan can only run on TCL tools"
    """
    This is needed to block top/bottom site rows and re-do wiring tracks.
    This resolves many DRCs and removes the need for the user to do it in placement constraints.
    """
    ht.append('''
# Need to delete and recreate tracks based on tech LEF pitches but overriding offsets
add_tracks -honor_pitch -offsets { M4 horiz 0.048 M5 vert 0.048 M6 horiz 0.064 M7 vert 0.064 }

# Create place blockage on top & bottom row, fixes wiring issue + power vias for DRC/LVS
set core_lly [get_db current_design .core_bbox.ll.y]
set core_ury [expr [get_db current_design .core_bbox.ur.y] - 1.08]
set botrow [get_db rows -if {.rect.ll.y == $core_lly}]
set toprow [get_db rows -if {.rect.ur.y > $core_ury}]
create_place_blockage -area [get_db $botrow .rect] -name ROW_BLOCK_BOT
create_place_blockage -area [get_db $toprow .rect] -name ROW_BLOCK_TOP
''')
    return True

def asap7_scale_final_gds(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerPlaceAndRouteTool), "asap7_scale_final_gds can only run on par"
    assert isinstance(ht, TCLTool), "asap7_scale_final_gds can only run on TCL tools"
    """
    Scale the final GDS by a factor of 4
    scale_gds_script writes the actual Python script to execute from the Tcl interpreter
    """
    # This is from the Cadence tools
    cadence_output_gds_name = ht.output_gds_filename  # type: ignore

    ht.append('''
# Innovus <19.1 appends some bad LD_LIBRARY_PATHS, so remove them before executing python
set env(LD_LIBRARY_PATH) [join [lsearch -not -all -inline [split $env(LD_LIBRARY_PATH) ":"] "*INNOVUS*"] ":"]
asap7_gds_scale {stdcells_file} {gds_file}
'''.format(stdcells_file = os.path.join(ht.technology.cache_dir, 'stdcells.txt'),
           gds_file = cadence_output_gds_name))
    return True

def asap7_generate_drc_run_file(ht: HammerTool) -> bool:
    assert isinstance(ht, HammerDRCTool), "asap7_generate_drc_run_file can only run on drc"
    assert isinstance(ht, MentorCalibreTool), "asap7_generate_drc_run_file can only run on a Calibre tool"
    """
    Replace drc_run_file to prevent conflicting SVRF statements
    Symlink DRC GDS to test.gds as expected by deck and results directories
    """
    # These are from the Calibre tools
    ht_drc_run_file = ht.drc_run_file  # type: ignore
    ht_max_drc_results = ht.max_drc_results  # type: ignore
    ht_virtual_connect_colon = ht.virtual_connect_colon  # type: ignore

    new_layout_file = os.path.join(ht.run_dir, 'test.gds')
    if not os.path.lexists(new_layout_file):
        os.symlink(os.path.splitext(ht.layout_file)[0] + '_drc.gds', new_layout_file)
    ht.layout_file = new_layout_file

    with open(ht_drc_run_file, "w") as f:
        f.write(textwrap.dedent("""
        // Generated by HAMMER

        DRC MAXIMUM RESULTS {max_results}
        DRC MAXIMUM VERTEX 4096

        DRC CELL NAME YES CELL SPACE XFORM

        VIRTUAL CONNECT COLON {virtual_connect}
        VIRTUAL CONNECT REPORT NO
        """).format(
            max_results=ht_max_drc_results,
            virtual_connect="YES" if ht_virtual_connect_colon else "NO"
        )
        )
        # Include paths to all supplied decks
        for rule in ht.get_drc_decks():
            f.write("INCLUDE \"{}\"\n".format(rule.path))
        # Note that an empty list means run all, and Calibre conveniently will do just that
        # if we don't specify any individual checks to run.
        if len(ht.drc_rules_to_run()) > 0:
            f.write("\nDRC SELECT CHECK\n")
        for check in ht.drc_rules_to_run():
            f.write("\t\"{}\"\n".format(check))
        f.write("\nDRC ICSTATION YES\n")
        f.write(ht.get_additional_drc_text())
    return True

tech = ASAP7Tech()
