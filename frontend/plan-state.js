// HA removes custom attributes while an entity is unavailable. Keep one
// received plan for presentation only; this never grants actuator permission.
export class PlanDisplay {
  read(entityId, state, hasPlan) {
    if (entityId !== this.entityId) {
      this.entityId = entityId;
      this.last = undefined;
    }
    if (!state) {
      this.last = undefined;
      return state;
    }
    const unavailable = ["unknown", "unavailable"].includes(state.state);
    if (!unavailable) {
      this.last = hasPlan(state) ? state : undefined;
      return state;
    }
    if (hasPlan(state) || !this.last) return state;
    return { ...state, attributes: this.last.attributes };
  }
}
