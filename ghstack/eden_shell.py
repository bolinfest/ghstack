#!/usr/bin/env python3

import logging
import os
from typing import Any, Optional, List

import ghstack
import ghstack.config
from ghstack.shell import _SHELL_RET

EDEN_CLI = "hg"
EDEN_PLAIN_ENV_VAR = "HGPLAIN"

WILDCARD_ARG = {}

class EdenShell(ghstack.shell.Shell):
    def __init__(self,
                 conf: ghstack.config.Config,
                 quiet: bool = False,
                 cwd: Optional[str] = None,
                 testing: bool = False):
        super().__init__(quiet=quiet, cwd=cwd, testing=testing)
        self.conf = conf

        self.git_dir = self._run_eden_command([
            'debugshell',
            '-c',
            'print(repo.svfs.join(repo.svfs.readutf8("gitdir")))',
        ])
        logging.debug(f"--git-dir set to: {self.git_dir}")

    def git(self, *_args: str, **kwargs: Any  # noqa: F811
            ) -> _SHELL_RET:
        args = list(_args)
        remote_name = self.conf.remote_name
        if match_args(["remote", "get-url", remote_name], args):
            return self._get_origin()
        elif match_args(["fetch", "--prune", remote_name], args):
            if len(args) != 3:
                raise Exception(f"expected exactly 3 args, but was: {args}")
            args[2] = self._get_origin()
            # Need to specify this explicitly because this does not appear to
            # be specified in the "config" folder in the bare Git clone.
            args.append('+refs/heads/*:refs/remotes/origin/*')
        elif match_args(["merge-base", WILDCARD_ARG, "HEAD"], args):
            # remote is probably "origin/main", which we need to convert to
            # "main" to use with the `log` subcommand.
            remote = args[1]
            index = remote.rfind('/')
            if index != -1:
                remote = remote[(index+1):]
            return self._run_eden_command(["log", "-T", "{node}", "-r", f"ancestor(., {remote})"])
        elif match_args(["push", remote_name], args):
            if len(args) == 2:
                raise Exception(f"expected more args: {args}")
            is_force = args[2] == '--force'
            branch_args = args[3 if is_force else 2:]
            # This command maps to multiple shell commands, so we return the
            # result of the last one.
            return self._push_branches(is_force, branch_args)


        git_args = self._rewrite_args(args)
        full_args = ["--git-dir", self.git_dir] + git_args
        return super().git(*full_args, **kwargs)

    def _push_branches(self, is_force: bool, branch_args: List[str]) -> _SHELL_RET:
        last_result = None
        for arg in branch_args:
            # split_point should be 40...
            split_point = arg.index(':')
            commit_hash = arg[:split_point]
            refspec = arg[(split_point+1):]
            prefix = 'refs/heads/'
            if refspec.startswith(prefix):
                refspec = refspec[len(prefix):]

            # Currently, it seems like Eden gets mad if the commit only exists
            # in the backing store as it was created "behind its back" via
            # `git commit-tree`, so we pull it into the client first to avoid
            # any errors...
            self._run_eden_command(["pull", "-r", commit_hash])

            full_args = ["push", "-r", commit_hash, "--to", refspec]

            # Ideally, we should only add --force when the original command was
            # `git push --force`, but for now, we always specify --force in an
            # attempt to get things working...
            full_args += ["--force"]
            # Do we need to add "--create"?
            last_result = self._run_eden_command(full_args)
        return last_result

    def _rewrite_args(self, _args: List[str]) -> List[str]:
        args = _args[:]

        # When running queries against a bare repo via `git --git-dir`, Git will
        # not be able to resolve arguments like HEAD, so we must resolve those
        # to a full hash before running Git.
        if 'HEAD' in args:
            top = self._run_eden_command(['log', '-r', 'max(.::)', '-T', '{node}'])
            for index, arg in enumerate(args):
                if arg == 'HEAD':
                    args[index] = top

        return args

    def _get_origin(self):
        # This should be good enough, right???
        return self._run_eden_command(["config", "paths.default"])

    def _run_eden_command(self, args: List[str]) -> str:
        env = dict(os.environ)
        env[EDEN_PLAIN_ENV_VAR] = "1"
        full_args = [EDEN_CLI] + args
        return self._maybe_rstrip(self.sh(*full_args, env=env))


def match_args(pattern, args: List[str]) -> bool:
    if len(pattern) > len(args):
        return False

    for pattern_arg, arg in zip(pattern, args):
        if pattern_arg is WILDCARD_ARG:
            continue
        elif isinstance(pattern_arg, str):
            if pattern_arg != arg:
                return False
        else:
            raise Exception(f"Unknown pattern type: {type(pattern_arg)}: {pattern_arg}")

    return True
