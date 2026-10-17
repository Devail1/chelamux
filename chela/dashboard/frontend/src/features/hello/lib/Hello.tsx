// The toolchain proof (CMX-176): one branch, for a mutation to break and the test to catch.
export function Hello({ name }: { name?: string }) {
  return <p>Hello, {name ? name : 'world'}!</p>;
}
