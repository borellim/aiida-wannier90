#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import copy

from aiida.common.orbital import OrbitalFactory
from aiida.common.utils import conv_to_fortran_withlists

from ._group_list import list_to_grouped_string

__all__ = ['write_win']


def write_win(
    filename,
    parameters,
    structure,
    kpoints,
    kpoints_path,
    projections,
):
    # prepare the main input text
    input_file_lines = []
    input_file_lines += _format_parameters(parameters)

    block_inputs = {}
    # take projections dict and write to file
    # checks if spins are used, and modifies the opening line
    projection_list = projections.get_orbitals()
    spin_use = any([bool(projection.get_orbital_dict()['spin'])
                    for projection in projection_list])
    if spin_use:
        raise InputValidationError("Spinors are implemented but not tested"
                                   "disable this error if you know what "
                                   "you are doing!")
        projector_type = "spinor_projections"
    else:
        projector_type = "projections"
    input_file_lines.append('Begin {}'.format(projector_type))
    for projection in projection_list:
        orbit_line = _create_wann_line_from_orbital(projection)
        input_file_lines.append(orbit_line)
    input_file_lines.append('End {}'.format(projector_type))

    # convert the structure data
    input_file_lines.append("Begin unit_cell_cart")
    input_file_lines.append('ang')
    for vector in structure.cell:
        input_file_lines.append("{0:18.10f} {1:18.10f} {2:18.10f}".format
                                (*vector))
    input_file_lines.append('End unit_cell_cart')

    input_file_lines.append('Begin atoms_cart')
    input_file_lines.append('ang')
    wann_positions, wann_kind_names = _wann_site_format(structure.sites)
    atoms_cart = zip(wann_kind_names, wann_positions)
    for atom in atoms_cart:
        input_file_lines.append('{}  {}'.format(atom[0], atom[1]))
    input_file_lines.append('End atoms_cart')

    # convert the kpoints_path
    try:
        special_points = kpoints_path.get_special_points()
    except ModificationNotAllowed:
        raise InputValidationError('kpoints_path must be kpoints with '
                                   'a special kpoint path already set!')

    input_file_lines.append('Begin Kpoint_Path')
    for (point1, point2) in special_points[1]:
        coord1 = special_points[0][point1]
        coord2 = special_points[0][point2]
        path_line = '{} {} {} {} '.format(point1, *coord1)
        path_line += ' {} {} {} {}'.format(point2, *coord2)
        input_file_lines.append(path_line)
    input_file_lines.append('End Kpoint_Path')

    # convert the kmesh
    try:
        kmesh = kpoints.get_kpoints_mesh()[0]
    except AttributeError:
        raise InputValidationError('kpoints should be set with '
                                   'set_kpoints_mesh, '
                                   'and not set_kpoints... ')

    mp_line = 'mp_grid = {},{},{}'.format(*kmesh)
    input_file_lines.append(mp_line)

    input_file_lines.append('Begin kpoints')
    for vector in kpoints.get_kpoints_mesh(print_list=True):
        input_file_lines.append("{0:18.10f} {1:18.10f} {2:18.10f}"
                                .format(*vector))
    input_file_lines.append('End kpoints')

    with open(filename, 'w') as file:
        file.write("\n".join(input_file_lines))
        file.write("\n")


def _format_parameters(parameters_dict):
    """
    Join key / value pairs of the parameters dictionary into formatted strings, returning a list of lines for the .win file.
    """
    lines = []
    for key, value in sorted(_format_values(parameters_dict).items()):
        lines.append(key + ' = ' + value)
    return lines


def _format_values(parameters_dict):
    """
    Turn the values of the parameters dictionary into the appropriate string.
    """
    result_dict = {}
    for key, value in parameters_dict.items():
        key = key.lower()
        if key == 'exclude_bands':
            result_dict[key] = list_to_grouped_string(value)
        else:
            result_dict[key] = conv_to_fortran_withlists(value)
    return result_dict


def _wann_site_format(structure_sites):
    """
    Generates site locations and cell dimensions
    in a manner that can be used by the wannier90 input script
    """
    def list2str(list_item):
        '''
        Converts an input list item into a str
        '''
        list_item = copy.deepcopy(list_item)
        if isinstance(list_item, (str, unicode)):
            return list_item
        else:
            return ' ' + ' '.join([str(_) for _ in list_item]) + ' '

    calc_positions = []
    calc_kind_names = []
    for i in range(len(structure_sites)):
        calc_positions.append(list2str(structure_sites[i].position))
        calc_kind_names.append(structure_sites[i].kind_name)
    return calc_positions, calc_kind_names


def _create_wann_line_from_orbital(orbital):
    """
    Creates an appropriate wannier line from input orbitaldata,
    will raise an exception if the orbital does not contain enough
    information, or the information is badly formated
    """
    RealhydrogenOrbital = OrbitalFactory("realhydrogen")

    if not isinstance(orbital, RealhydrogenOrbital):
        raise InputValidationError(
            "Only realhydrogen orbitals are currently supported for Wannier90 input.")
    orb_dict = copy.deepcopy(orbital.get_orbital_dict())

    def _get_attribute(name, required=True):
        res = orb_dict.get(name, None)
        if res is None and required:
            raise InputValidationError("Orbital is missing attribute '{}'.".format(name))
        return res

    position = _get_attribute("position")
    wann_string = "c=" + ",".join([str(x) for x in position])

    angular_momentum = _get_attribute("angular_momentum")
    wann_string += ":l={}".format(str(angular_momentum))

    magnetic_number = _get_attribute("magnetic_number")
    wann_string += ",mr={}".format(str(magnetic_number + 1))

    # orientations, optional
    zaxis = _get_attribute("z_orientation", required=False)
    if zaxis is not None:
        wann_string += ":z=" + ",".join([str(x) for x in zaxis])
    xaxis = _get_attribute("x_orientation", required=False)
    if xaxis is not None:
        wann_string += ":x=" + ",".join([str(x) for x in xaxis])

    radial = _get_attribute("radial_nodes", required=False)
    if radial is not None:
        wann_string += ":r={}".format(str(radial + 1))

    zona = _get_attribute("diffusivity", required=False)
    if zona is not None:
        wann_string += ":{}".format(str(zona))

    # spin, optional
    # Careful with spin, it is insufficient to set the spin the projection
    # line alone. You must, in addition, apply the appropriate settings:
    # either set spinors=.true. or use spinor_projections, see user guide
    spin = _get_attribute("spin", required=False)
    if spin is not None:
        spin_dict = {-1: "d", 1: "u"}
        wann_string += "({})".format(spin_dict[spin])
    spin_orient = _get_attribute("spin_orientation", required=False)
    if spin_orient is not None:
        wann_string += "[" + ",".join([str(x) for x in spin_orient]) + "]"

    return wann_string