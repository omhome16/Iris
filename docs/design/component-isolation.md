# Component isolation

Two boundaries exist. They are not the same thing.

The check (`check_in_sandbox`) runs before approval. On Linux it can apply
Landlock, seccomp, and a network namespace. Where that kernel support is
missing, the check is an audit hook and the detail says `isolation=audit`.
Approving a component does not keep that jail.

`COMPONENT_HOST=subprocess` runs context, capture, and consolidator in a
child process after approval. The child starts with a scrubbed environment,
the Iris kernel is taken off `sys.path`, and an audit hook refuses reads
outside the interpreter, the component, and its state directory. Model,
memory, and file calls are requests back to the parent, and the parent
answers only the grants in `component.toml`. The default is `in-process`.
Persona and memory backends stay in-process either way.

That child is not a kernel jail. A bug in the audit hook, or a platform
that does not enforce it, is not Landlock. Do not describe it as one.
`net.request` is not a grant. Windows and macOS do not grow a kernel jail
by turning the host on.

Build the Linux host on top of the existing check jail when a component the
owner did not read must be contained by the kernel, not only by the audit hook.
