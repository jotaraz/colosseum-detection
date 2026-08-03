# Report to cluster support — GPU nodes i101 and i104 accept jobs but have unusable GPUs

**Reporter:** jtaraz
**Date observed:** 2026-08-02 / 2026-08-03
**Severity:** medium — no data loss on your side, but jobs are silently accepted onto hardware that
cannot run them, and they consume their full allocation before failing.

---

## Summary

`i101.internal.cluster.is.localnet` and `i104.internal.cluster.is.localnet` are advertising healthy
H100-80GB slots and accepting GPU jobs, but on both machines the GPUs cannot be initialised. Any job
scheduled there runs to completion while every CUDA operation fails.

Two of my six 4-GPU jobs landed on these nodes and produced nothing usable, holding 4 H100s each for
the duration. A one-CPU probe job submitted to both machines reproduces the fault in seconds.

## Evidence

A minimal probe job (`request_cpus = 1`, no GPU request), constrained to the two machines, exited 0
on both and reported:

```
NODE=i101
Unable to determine the device handle for GPU0: 0000:23:00.0: Unknown Error
No devices were found

NODE=i104
Unable to determine the device handle for GPU0: 0000:C3:00.0: Unknown Error
No devices were found
```

`Unable to determine the device handle ... Unknown Error` is a driver-level fault (the device is
enumerated but unreachable), distinct from the "no devices" a job without a GPU request would
normally see. The two PCI addresses differ (`23:00.0` on i101, `C3:00.0` on i104), so this is not a
single shared device.

From the affected jobs' stderr, `torch` fails at the same point:

```
torch/cuda/__init__.py:1061: UserWarning: Can't initialize NVML
torch/cuda/__init__.py:1113: UserWarning: CUDA initialization: CUDA ...
```

Consequently every vLLM server launch failed:

```
vLLM server for model 'qwen_qwen3_6_35b_a3b' exited prematurely with code 1
vLLM server for model 'openai_gpt_oss_120b' exited prematurely with code 1
```

Meanwhile HTCondor still advertised the slots as normal:

```
i101.internal.cluster.is.localnet  Claimed    Busy  NVIDIA H100 80GB HBM3
i104.internal.cluster.is.localnet  Unclaimed  Idle  NVIDIA H100 80GB HBM3
```

## Affected jobs

| ClusterId | node | request_gpus | outcome |
|---|---|---|---|
| 17432243 | i101 | 4 | ran to completion; 36/36 rollouts failed, zero usable output |
| 17432244 | i104 | 4 | ran to completion; 48/48 rollouts failed, zero usable output |
| 17432233 | i101 | 4 | earlier attempt, same node (failed for an unrelated reason) |
| 17432234 | i104 | 4 | earlier attempt, same node (failed for an unrelated reason) |
| 17432370 | i101 + i104 | 0 | diagnostic probe, output quoted above |

Both jobs exited 0. Nothing in the HTCondor record marks them as failed — the failure is only
visible inside the application's own artifacts.

## What we would find helpful

1. **Check and reset the GPUs on i101 and i104.** From the outside this looks like a device that has
   fallen off the bus or is wedged after an Xid error; a driver reload or node reboot usually clears
   it. If the hardware is genuinely faulty, draining the slots would be preferable to leaving them
   advertised.

2. **If possible, have the startd stop advertising GPU slots it cannot initialise.** A periodic
   health check that runs something as cheap as `nvidia-smi -L` and marks the slot unavailable on
   failure would have prevented both incidents entirely. As it stands, a node in this state is
   *attractive* to the scheduler — it is idle, so it gets picked first, and it keeps accepting work
   it cannot do.

3. **Is there an existing way for users to detect or avoid this?** We did not find a machine ClassAd
   attribute reflecting GPU health, so our only options were to exclude the nodes by name after the
   fact. If such an attribute exists (or could exist), we would gladly filter on it in
   `requirements` rather than maintaining a hand-written deny list.

4. **No refund is requested**, but for your records: roughly 8 H100-hours were consumed producing
   nothing. If bank charges for such jobs can be identified, others may be affected too — this
   failure mode is invisible to any workflow that only checks exit codes.

## What we changed on our side

Not required from you, but for context, so you know we are not going to keep resubmitting into it:

- Jobs now preflight the GPUs (`nvidia-smi -L`, then a small tensor allocation on each of the four
  devices) and exit immediately with code 42 on failure, instead of running for hours.
- Our optimizer now aborts after three consecutive steps in which every rollout fails, rather than
  recording them as legitimate zero-valued results.
- `i101` and `i104` are excluded by name in our `requirements` until we hear that they are fixed.

Happy to rerun the probe, share the full job logs, or test the nodes once they have been looked at —
just let us know.

**Contact:** jtaraz
**Relevant paths:** submit files and job logs under
`/fast/jtaraz/LIARS/colosseum-detection/experiments/social_jira4/cluster/`; probe output at
`/fast/jtaraz/gpuprobe_17432370_*.out`.
