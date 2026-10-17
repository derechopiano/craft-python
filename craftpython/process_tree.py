"""Own only this macro worker's process group; no process-name scans or global kills."""
import os
import signal


class ProcessTree:
    def __init__(self):
        self.process = None
        self.job = _WindowsJob() if os.name == 'nt' else None

    def attach(self, process):
        self.process = process
        if self.job is not None:
            self.job.attach(process.pid)

    def close(self):
        if self.job is not None:
            self.job.close()
        elif self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process = None


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes as w

    class _BasicLimits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', w.DWORD), ('minimum_ws', ctypes.c_size_t),
                    ('maximum_ws', ctypes.c_size_t), ('active_processes', w.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class _IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', _BasicLimits), ('io', _IOCounters),
                    ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    _kernel.CreateJobObjectW.restype = w.HANDLE
    _kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    _kernel.SetInformationJobObject.restype = w.BOOL
    _kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    _kernel.AssignProcessToJobObject.restype = w.BOOL
    _kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    _kernel.OpenProcess.restype = w.HANDLE
    _kernel.CloseHandle.argtypes = [w.HANDLE]
    _kernel.CloseHandle.restype = w.BOOL

    class _WindowsJob:
        def __init__(self):
            self.handle = _kernel.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, no breakaway.
            if not _kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

        def attach(self, pid):
            process = _kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _kernel.AssignProcessToJobObject(self.handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _kernel.CloseHandle(process)

        def close(self):
            if self.handle:
                if not _kernel.CloseHandle(self.handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.handle = None
