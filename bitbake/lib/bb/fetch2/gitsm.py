# ex:ts=4:sw=4:sts=4:et
# -*- tab-width: 4; c-basic-offset: 4; indent-tabs-mode: nil -*-
"""
BitBake 'Fetch' git submodules implementation

Inherits from and extends the Git fetcher to retrieve submodules of a git repository
after cloning.

SRC_URI = "gitsm://<see Git fetcher for syntax>"

See the Git fetcher, git://, for usage documentation.

NOTE: Switching a SRC_URI from "git://" to "gitsm://" requires a clean of your recipe.

"""

# Copyright (C) 2013 Richard Purdie
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import os
import bb
import copy
from   bb.fetch2.git import Git
from   bb.fetch2 import runfetchcmd
from   bb.fetch2 import logger
from   bb.fetch2 import Fetch
from   bb.fetch2 import BBFetchException

class GitSM(Git):
    def supports(self, ud, d):
        """
        Check to see if a given url can be fetched with git.
        """
        return ud.type in ['gitsm']

    def process_submodules(self, ud, workdir, function, d):
        """
        Iterate over all of the submodules in this repository and execute
        the 'function' for each of them.
        """

        submodules = []
        paths = {}
        revision = {}
        uris = {}
        subrevision = {}

        def parse_gitmodules(gitmodules):
            modules = {}
            module = ""
            for line in gitmodules.splitlines():
                if line.startswith('[submodule'):
                    module = line.split('"')[1]
                    modules[module] = {}
                elif module and line.strip().startswith('path'):
                    path = line.split('=')[1].strip()
                    modules[module]['path'] = path
                elif module and line.strip().startswith('url'):
                    url = line.split('=')[1].strip()
                    modules[module]['url'] = url
            return modules

        # Collect the defined submodules, and their attributes
        for name in ud.names:
            try:
                gitmodules = runfetchcmd("%s show %s:.gitmodules" % (ud.basecmd, ud.revisions[name]), d, quiet=True, workdir=workdir)
            except:
                # No submodules to update
                continue

            for m, md in parse_gitmodules(gitmodules).items():
                try:
                    module_hash = runfetchcmd("%s ls-tree -z -d %s %s" % (ud.basecmd, ud.revisions[name], md['path']), d, quiet=True, workdir=workdir)
                except:
                    # If the command fails, we don't have a valid file to check.  If it doesn't
                    # fail -- it still might be a failure, see next check...
                    module_hash = ""

                if not module_hash:
                    logger.debug(1, "submodule %s is defined, but is not initialized in the repository. Skipping", m)
                    continue

                submodules.append(m)
                paths[m] = md['path']
                revision[m] = ud.revisions[name]
                uris[m] = md['url']
                subrevision[m] = module_hash.split()[2]

                # Convert relative to absolute uri based on parent uri
                if uris[m].startswith('..'):
                    newud = copy.copy(ud)
                    newud.path = os.path.realpath(os.path.join(newud.path, uris[m]))
                    uris[m] = Git._get_repo_url(self, newud)

        for module in submodules:
            # Translate the module url into a SRC_URI

            if "://" in uris[module]:
                # Properly formated URL already
                proto = uris[module].split(':', 1)[0]
                url = uris[module].replace('%s:' % proto, 'gitsm:', 1)
            else:
                if ":" in uris[module]:
                    # Most likely an SSH style reference
                    proto = "ssh"
                    if ":/" in uris[module]:
                        # Absolute reference, easy to convert..
                        url = "gitsm://" + uris[module].replace(':/', '/', 1)
                    else:
                        # Relative reference, no way to know if this is right!
                        logger.warning("Submodule included by %s refers to relative ssh reference %s.  References may fail if not absolute." % (ud.url, uris[module]))
                        url = "gitsm://" + uris[module].replace(':', '/', 1)
                else:
                    # This has to be a file reference
                    proto = "file"
                    url = "gitsm://" + uris[module]

            url += ';protocol=%s' % proto
            url += ";name=%s" % module
            url += ";subpath=%s" % module

            ld = d.createCopy()
            # Not necessary to set SRC_URI, since we're passing the URI to
            # Fetch.
            #ld.setVar('SRC_URI', url)
            ld.setVar('SRCREV_%s' % module, subrevision[module])

            # Workaround for issues with SRCPV/SRCREV_FORMAT errors
            # error refer to 'multiple' repositories.  Only the repository
            # in the original SRC_URI actually matters...
            ld.setVar('SRCPV', d.getVar('SRCPV'))
            ld.setVar('SRCREV_FORMAT', module)

            function(ud, url, module, paths[module], ld)

        return submodules != []

    def download(self, ud, d):
        def download_submodule(ud, url, module, modpath, d):
            url += ";bareclone=1;nobranch=1"

            # Is the following still needed?
            #url += ";nocheckout=1"

            try:
                newfetch = Fetch([url], d, cache=False)
                newfetch.download()
            except Exception as e:
                logger.error('gitsm: submodule download failed: %s %s' % (type(e).__name__, str(e)))
                raise

        Git.download(self, ud, d)
        self.process_submodules(ud, ud.clonedir, download_submodule, d)

    def unpack(self, ud, destdir, d):
        def unpack_submodules(ud, url, module, modpath, d):
            url += ";bareclone=1;nobranch=1"

            # Figure out where we clone over the bare submodules...
            if ud.bareclone:
                repo_conf = ud.destdir
            else:
                repo_conf = os.path.join(ud.destdir, '.git')

            try:
                newfetch = Fetch([url], d, cache=False)
                newfetch.unpack(root=os.path.dirname(os.path.join(repo_conf, 'modules', module)))
            except Exception as e:
                logger.error('gitsm: submodule unpack failed: %s %s' % (type(e).__name__, str(e)))
                raise

            local_path = newfetch.localpath(url)

            # Correct the submodule references to the local download version...
            runfetchcmd("%(basecmd)s config submodule.%(module)s.url %(url)s" % {'basecmd': ud.basecmd, 'module': module, 'url' : local_path}, d, workdir=ud.destdir)

            if ud.shallow:
                runfetchcmd("%(basecmd)s config submodule.%(module)s.shallow true" % {'basecmd': ud.basecmd, 'module': module}, d, workdir=ud.destdir)

            # Ensure the submodule repository is NOT set to bare, since we're checking it out...
            try:
                runfetchcmd("%s config core.bare false" % (ud.basecmd), d, quiet=True, workdir=os.path.join(repo_conf, 'modules', module))
            except:
                logger.error("Unable to set git config core.bare to false for %s" % os.path.join(repo_conf, 'modules', module))
                raise

        Git.unpack(self, ud, destdir, d)

        ret = self.process_submodules(ud, ud.destdir, unpack_submodules, d)

        if not ud.bareclone and ret:
            # All submodules should already be downloaded and configured in the tree.  This simply sets
            # up the configuration and checks out the files.  The main project config should remain
            # unmodified, and no download from the internet should occur.
            runfetchcmd("%s submodule update --recursive --no-fetch" % (ud.basecmd), d, quiet=True, workdir=ud.destdir)
