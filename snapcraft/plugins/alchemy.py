# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2016 Parrot SA
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Alchemy plugin."""

import snapcraft
from snapcraft import sources
from snapcraft.pluginhandler import _make_options
from snapcraft import common

import os
import stat
import tempfile
import shutil
from elftools.elf.elffile import ELFFile
from elftools.elf.segments import InterpSegment


class AlchemyPlugin(snapcraft.BasePlugin):

    @classmethod
    def schema(cls):
        schema = super().schema()
        schema['properties']['makefile'] = {
            'type': 'string'
        }

        schema['properties']['dependencies'] = {
            'type': 'string',
            'default': ''
        }

        return schema

    def __init__(self, name, options):
        super().__init__(name, options)
        self.build_packages.extend(['autoconf',
                                    'automake',
                                    'autopoint',
                                    'libtool',
                                    'make',
                                    'cmake',
                                    'python3-pyelftools',
                                    'python'])

    def pull(self):
        if 'NO_CLONE' not in os.environ:
            super().pull()

        if 'ALCHEMY_PATH' not in os.environ:
            options = {
                'source-type': 'git',
                'source': 'https://github.com/Parrot-Developers/alchemy.git'
            }
            alchemy_partdir = os.path.join(common.get_partsdir(), 'alchemy')
            sourcedir = os.path.join(alchemy_partdir, 'src')
            builddir = os.path.join(alchemy_partdir, 'build')
            sources.get(sourcedir, builddir, _make_options({}, options, snapcraft.BasePlugin.schema()))

    def build(self):
        super().build()

        if 'ALCHEMY_PATH' in os.environ:
            alchemy_dir = os.environ['ALCHEMY_PATH']
        else:
            alchemy_dir = os.path.join(common.get_partsdir(), 'alchemy', 'src')
        alchemake = os.path.join(alchemy_dir, 'scripts', 'alchemake.py')

        os.environ['ALCHEMY_WORKSPACE_DIR'] = os.path.join(common.get_partsdir(), self.name, 'build')
        os.environ['TARGET_OUT'] = common.get_partsdir()
        # tell Alchemy we only build the targeted package (avoid $(ERROR) in wlanconfig package)
        os.environ['ALCHEMY_TARGET_CONFIG_DIR'] = tempfile.mkdtemp()
        open(os.path.join(os.environ['ALCHEMY_TARGET_CONFIG_DIR'], 'global.config'), 'a')
        if 'TARGET_CROSS' not in os.environ:
            os.environ['TARGET_CROSS'] = '/opt/arm-2014.11-linaro/bin/arm-linux-gnueabihf-'

        command = [alchemake, '-f', os.path.join(alchemy_dir, 'main.mk'), '-C', os.environ['ALCHEMY_WORKSPACE_DIR'],
                   self.name, self.options.dependencies, 'final']
        self.run(command)
        alchemy_final = os.path.join(os.environ['TARGET_OUT'], 'final', '*')
        install = os.path.join(common.get_partsdir(), self.name, 'install')
        command = ['cp', '-r', alchemy_final, install]
        self.run(command)
        # generate shell wrappers for dynamically linked ELF to run them with embedded dynamic linker
        for root, dirs, files in os.walk(self.installdir):
            for file in files:
                filepath = os.path.join(root, file)
                with open(filepath, 'rb') as filed:
                    try:
                        e = ELFFile(filed)
                        if e.header.e_type == 'ET_EXEC':
                            is_dynamic_elf = False
                            for s in e.iter_segments():
                                if isinstance(s, InterpSegment):  # This ELF is a dynamically linked one
                                    dynamic_linker_path = s.get_interp_name()
                                    is_dynamic_elf = True
                                    break
                            if is_dynamic_elf:
                                del e
                                filed.close()
                                wrapped_path = os.path.join(root, file + '_wrapped')
                                self.run(['mv', filepath, wrapped_path])
                                target_path = wrapped_path.replace(self.installdir, '')
                                filed = open(filepath, 'w+')
                                filed.write("""#!/bin/sh
export ROOTFS_RO=$SNAP
export ROOTFS_RW=$SNAP_DATA
exec \"$SNAP/""" + dynamic_linker_path.decode('ascii') + '\" \"$SNAP/' + target_path + '\" \"$@\"\n')
                                filed.close()
                                st = os.stat(filepath)
                                os.chmod(filepath, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                                is_dynamic_elf = False
                    except Exception as e:
                        pass
        shutil.rmtree(os.environ['ALCHEMY_TARGET_CONFIG_DIR'])
