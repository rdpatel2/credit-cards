"""Process-lifetime directory lease for the explicit service/operator CLI.

The lock file remains in place: unlinking it could let a second process lock
a different inode while the first process still owns its descriptor.
"""
import os
from pathlib import Path


class DirectoryLease:
    def __init__(self,directory):self.directory=Path(directory);self.file=None
    def __enter__(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        try:self.directory.chmod(0o700)
        except OSError:pass
        handle=open(self.directory/'service.lock','a+b')
        try:
            if os.name=='posix':
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            elif os.name=='nt':
                import msvcrt
                if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:raise ValueError('This platform cannot enforce the service directory lease.')
            try:os.chmod(handle.name,0o600)
            except OSError:pass
        except OSError:
            handle.close();raise ValueError('Another service/operator process owns this directory. Stop it before starting another service or repairing consent.') from None
        except BaseException:handle.close();raise
        self.file=handle;return self
    def __exit__(self,*exc):
        if self.file:self.file.close();self.file=None
