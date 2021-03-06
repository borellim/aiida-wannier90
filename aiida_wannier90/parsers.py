# -*- coding: utf-8 -*-
from __future__ import absolute_import
import io
import os
import six
from six.moves import range
from aiida.parsers import Parser
from aiida.common import exceptions as exc


class Wannier90Parser(Parser):
    """
    Wannier90 output parser. Will parse global gauge invarient spread as well as
    the centers, spreads and, if possible the Imaginary/Real ratio of the
    wannier functions. Will also check to see if the output converged.
    """
    def __init__(self, node):
        from .calculations import Wannier90Calculation

        # check for valid input
        if not issubclass(node.process_class, Wannier90Calculation):
            raise exc.OutputParsingError(
                "Input must calc must be a "
                "Wannier90Calculation, it is instead {}".format(
                    type(node.process_class)
                )
            )
        super(Wannier90Parser, self).__init__(node)

    def parse(self, **kwargs):
        """
        Parses the datafolder, stores results.
        This parser for this simple code does simply store in the DB a node
        representing the file of forces in real space
        """
        from aiida.orm import Dict, SinglefileData

        # None if unset
        temporary_folder = kwargs.get('retrieved_temporary_folder')

        seedname = self.node.get_options()['seedname']
        output_file_name = "{}.wout".format(seedname)
        error_file_name = "{}.werr".format(seedname)
        nnkp_file_name = "{}.nnkp".format(seedname)

        # select the folder object
        # Check that the retrieved folder is there
        try:
            out_folder = self.retrieved
        except exc.NotExistent:
            return self.exit_codes.ERROR_NO_RETRIEVED_FOLDER

        # Checks for error output files
        if error_file_name in out_folder.list_object_names():
            self.logger.error(
                'Errors were found please check the retrieved '
                '{} file'.format(error_file_name)
            )
            return self.exit_codes.ERROR_WERR_FILE_PRESENT

        exiting_in_stdout = False
        try:
            with out_folder.open(output_file_name) as handle:
                out_file = handle.readlines()
            # Wannier90 doesn't always write the .werr file on error
            if any('Exiting......' in line for line in out_file):
                exiting_in_stdout = True
        except OSError:
            self.logger.error("Standard output file could not be found.")
            return self.exit_codes.ERROR_OUTPUT_STDOUT_MISSING

        if temporary_folder is not None:
            nnkp_temp_path = os.path.join(temporary_folder, nnkp_file_name)
            if os.path.isfile(nnkp_temp_path):
                with io.open(nnkp_temp_path, 'rb') as handle:
                    node = SinglefileData(file=handle)
                    self.out('nnkp_file', node)

        # Tries to parse the bands
        try:
            kpoint_path = self.node.inputs.kpoint_path
            special_points = kpoint_path.get_dict()
            with out_folder.open('{}_band.dat'.format(seedname)) as fil:
                band_dat_file = fil.readlines()
            with out_folder.open('{}_band.kpt'.format(seedname)) as fil:
                band_kpt_file = fil.readlines()
        except (exc.NotExistent, KeyError, IOError):
            # exc.NotExistent: no input kpoint_path
            # KeyError: no get_dict()
            # IOError: _band.* files not present
            pass
        else:
            structure = self.node.inputs.structure
            ## TODO: should we catch exceptions here?
            output_bandsdata = band_parser(
                band_dat_file, band_kpt_file, special_points, structure
            )
            self.out('interpolated_bands', output_bandsdata)

        # Parse the stdout an return the parsed data
        wout_dictionary = raw_wout_parser(out_file)
        output_data = Dict(dict=wout_dictionary)
        self.out('output_parameters', output_data)

        if exiting_in_stdout:
            return self.exit_codes.ERROR_EXITING_MESSAGE_IN_STDOUT


def raw_wout_parser(wann_out_file):
    '''
    This section will parse a .wout file and return certain key
    parameters such as the centers and spreads of the
    wannier90 functions, the Im/Re ratios, certain warnings,
    and labels indicating output files produced

    :param out_file: the .wout file, as a list of strings
    :return out: a dictionary of parameters that can be stored as parameter data
    '''
    w90_conv = False  #Used to assess convergence of MLWF procedure use conv_tol and conv_window>1
    out = {}
    out.update({'warnings': []})
    for i in range(len(wann_out_file)):
        line = wann_out_file[i]
        # checks for any warnings
        if 'Warning' in line:
            # Certain warnings get a special flag
            out['warnings'].append(line)

        # From the 'initial' part of the output, only sections which indicate
        # whether certain files have been written, e.g. 'Write r^2_nm to file'
        # the units used, e.g. 'Length Unit', that will guide the parser
        # e.g. 'Number of Wannier Functions', or which supplament warnings
        # not directly provided, e.g. unconvergerged wannierization needs
        # some logic in AiiDa to determine whether it met the convergence
        # target or not...

        # Parses some of the MAIN parameters
        if 'MAIN' in line:
            i += 1
            line = wann_out_file[i]
            while '-----' not in line:
                line = wann_out_file[i]
                if 'Number of Wannier Functions' in line:
                    out.update({
                        'number_wannier_functions':
                        int(line.split()[-2])
                    })
                if 'Length Unit' in line:
                    out.update({'length_units': line.split()[-2]})
                    if (out['length_units'] != 'Ang'):
                        out['warnings'].append(
                            'Units not Ang, '
                            'be sure this is OK!'
                        )

                if 'Output verbosity (1=low, 5=high)' in line:
                    out.update({'output_verbosity': int(line.split()[-2])})
                    if out['output_verbosity'] != 1:
                        out['warnings'].append(
                            'Parsing is only supported '
                            'directly supported if output verbosity is set to 1'
                        )
                if 'Post-processing' in line:
                    out.update({'preprocess_only': line.split()[-2]})
                i += 1

        # Parses some of the WANNIERISE parameters
        if 'WANNIERISE' in line:
            i += 1
            line = wann_out_file[i]
            while '-----' not in line:
                line = wann_out_file[i]
                if 'Convergence tolerence' in line:
                    out.update({
                        'wannierise_convergence_tolerance':
                        float(line.split()[-2])
                    })
                if 'Write r^2_nm to file' in line:
                    out.update({'r2_nm_writeout': line.split()[-2]})
                    if out['r2_nm_writeout'] != 'F':
                        out['warnings'].append(
                            'The r^2_nm file has been selected '
                            'to be written, but this is not yet supported!'
                        )

                if 'Write xyz WF centres to file' in line:
                    out.update({'xyz_wf_center_writeout': line.split()[-2]})
                    if out['xyz_wf_center_writeout'] != 'F':
                        out['warnings'].append(
                            'The xyz_WF_center file has '
                            'been selected to be written, but this is not '
                            'yet supported!'
                        )

                i += 1
        if 'Wannierisation convergence criteria satisfied' in line:
            w90_conv = True

        # Reading the final WF, also checks to see if they converged or not
        if 'Final State' in line:
            # Originally wanted to implement automatic convergence check
            # but parsing this using the version below fails depending
            # on the convergence settings used in the aiida.win file
            # Final_check_line = wann_out_file[i-2]
            # if  'Wannierisation convergence criteria satisfied' \
            #         not in Final_check_line:
            #     Final_Delta = float(Final_check_line.split()[-3])
            #     if abs(Final_Delta) > out['wannierise_convergence_tolerance']:
            #         out['Warnings'] += ['Wannierization not converged within '
            #         'specified tolerance!']
            num_wf = out['number_wannier_functions']
            wf_out = []
            end_wf_loop = i + num_wf + 1
            for i in range(i + 1, end_wf_loop):
                line = wann_out_file[i]
                wf_out_i = {
                    'wannier_function': '',
                    'coordinates': '',
                    'spread': ''
                }
                #wf_out_i['wannier_function'] = int(line.split()[-7])
                wf_out_i['wannier_function'] = int(
                    line.split('(')[0].split()[-1]
                )
                wf_out_i['spread'] = float(line.split(')')[1].strip())
                #wf_out_i['spread'] = float(line.split()[-1])
                try:
                    x = float(
                        line.split('(')[1].split(')')[0].split(',')[0].strip()
                    )
                except (ValueError, IndexError):
                    # To avoid that the crasher completely fails, we set None as a fallback
                    x = None
                try:
                    y = float(
                        line.split('(')[1].split(')')[0].split(',')[1].strip()
                    )
                except (ValueError, IndexError):
                    y = None
                try:
                    z = float(
                        line.split('(')[1].split(')')[0].split(',')[2].strip()
                    )
                except (ValueError, IndexError):
                    z = None
                coord = (x, y, z)
                wf_out_i['coordinates'] = coord
                wf_out.append(wf_out_i)
            out.update({'wannier_functions_output': wf_out})
            for i in range(i + 2, i + 6):
                line = wann_out_file[i]
                if 'Omega I' in line:
                    out.update({'Omega_I': float(line.split()[-1])})
                if 'Omega D' in line:
                    out.update({'Omega_D': float(line.split()[-1])})
                if 'Omega OD' in line:
                    out.update({'Omega_OD': float(line.split()[-1])})
                if 'Omega Total' in line:
                    out.update({'Omega_total': float(line.split()[-1])})

        if ' Maximum Im/Re Ratio' in line:
            wann_functions = out['wannier_functions_output']
            wann_id = int(line.split()[3])
            wann_function = wann_functions[wann_id - 1]
            wann_function.update({'im_re_ratio': float(line.split()[-1])})
    if not w90_conv:
        out['warnings'].append(
            'Wannierisation finished because num_iter was reached.'
        )
    return out


def band_parser(band_dat_path, band_kpt_path, special_points, structure):
    """
    Parsers the bands output data, along with the special points retrieved
    from the input kpoints to construct a BandsData object which is then
    returned. Cannot handle discontinuities in the kpath, if two points are
    assigned to same spot only one will be passed.

    :param band_dat_path: file path to the aiida_band.dat file
    :param band_kpt_path: file path to the aiida_band.kpt file
    :param special_points: special points to add labels to the bands a dictionary in
        the form expected in the input as described in the wannier90 documentation
    :return: BandsData object constructed from the input params
    """
    import numpy as np
    from aiida.orm.nodes.array.bands import BandsData
    from aiida.orm.nodes.array.kpoints import KpointsData

    # imports the data
    out_kpt = np.genfromtxt(band_kpt_path, skip_header=1, usecols=(0, 1, 2))
    out_dat = np.genfromtxt(band_dat_path, usecols=1)

    # reshaps the output bands
    out_dat = out_dat.reshape(
        len(out_kpt), (len(out_dat) / len(out_kpt)), order="F"
    )

    # finds expected points of discontinuity
    kpath = special_points['path']
    cont_break = [(i, (kpath[i - 1][1], kpath[i][0]))
                  for i in range(1, len(kpath))
                  if kpath[i - 1][1] != kpath[i][0]]

    # finds the special points
    special_points_dict = special_points['point_coords']
    # We set atol to 1e-5 because in the kpt file the coords are printed with fixed precision
    labels = [
        (i, k) for k in special_points_dict for i in range(len(out_kpt))
        if all(
            np.isclose(special_points_dict[k], out_kpt[i], rtol=0, atol=1.e-5)
        )
    ]
    labels.sort()

    # Checks and appends labels if discontinuity
    appends = []
    for x in cont_break:
        # two cases the break is before or the break is after
        # if the break is before
        if labels[x[0]][1] != x[1][0]:
            # checks to see if the discontinuity was already there
            if labels[x[0] - 1] == x[1][0]:
                continue
            else:
                insert_point = x[0]
                new_label = x[1][0]
                kpoint = labels[x[0]][0] - 1
                appends += [[insert_point, new_label, kpoint]]
        # if the break is after
        if labels[x[0]][1] != x[1][1]:
            # checks to see if the discontinuity was already there
            if labels[x[0] + 1] == x[1][1]:
                continue
            else:
                insert_point = x[0] + 1
                new_label = x[1][1]
                kpoint = labels[x[0]][0] + 1
                appends += [[insert_point, new_label, kpoint]]
    appends.sort()
    for i in range(len(appends)):
        append = appends[i]
        labels.insert(append[0] + i, (append[2], six.text_type(append[1])))
    bands = BandsData()
    k = KpointsData()
    k.set_cell_from_structure(structure)
    k.set_kpoints(out_kpt, cartesian=False)
    bands.set_kpointsdata(k)
    bands.set_bands(out_dat, units='eV')
    bands.labels = labels
    return bands
