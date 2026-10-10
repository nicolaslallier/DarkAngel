<script setup lang="ts">
import { onMounted, ref } from 'vue'

import type { Role } from '@/api/household'
import { useHouseholdStore } from '@/stores/household'

const store = useHouseholdStore()
const name = ref('')
const inviteRole = ref<'member' | 'viewer'>('member')
const link = ref('')

const LABELS: Record<Role, string> = { owner: 'Owner', member: 'Member', viewer: 'Read-only' }

onMounted(() => store.load())

async function create() {
  if (name.value.trim()) await store.create(name.value.trim())
}

async function invite() {
  const invitation = await store.invite(inviteRole.value)
  if (invitation) {
    link.value = `${location.origin}/household/join#token=${encodeURIComponent(invitation.token)}`
  }
}

async function destroy() {
  if (confirm('Delete the household with every provider, service and invoice?')) await store.remove()
}

async function leave() {
  if (confirm('Leave this household?')) await store.leave()
}
</script>

<template>
  <section>
    <h1>Household</h1>
    <p v-if="store.error" class="error">{{ store.error }}</p>

    <form v-if="store.loaded && !store.household" @submit.prevent="create">
      <p>
        You are not in a household yet. Create one, or open the invitation link someone sent you.
      </p>
      <input v-model="name" name="household-name" placeholder="Household name" maxlength="100" />
      <button type="submit">Create household</button>
    </form>

    <template v-if="store.household">
      <h2>{{ store.household.name }}</h2>
      <ul>
        <li v-for="member in store.household.members" :key="member.sub">
          <code>{{ member.sub }}</code>
          <template v-if="store.isOwner && member.role !== 'owner'">
            <select
              :value="member.role"
              @change="
                store.setRole(member.sub, ($event.target as HTMLSelectElement).value as 'member' | 'viewer')
              "
            >
              <option value="member">Member</option>
              <option value="viewer">Read-only</option>
            </select>
            <button type="button" @click="store.removeMember(member.sub)">Remove</button>
          </template>
          <span v-else>{{ LABELS[member.role] }}</span>
        </li>
      </ul>

      <div v-if="store.isOwner">
        <h3>Invite someone</h3>
        <select v-model="inviteRole" name="invite-role">
          <option value="member">Member</option>
          <option value="viewer">Read-only</option>
        </select>
        <button type="button" data-test="invite" @click="invite">Create invitation link</button>
        <p v-if="link">
          Send this link yourself; it works once and expires in 7 days.
          <input data-test="invite-link" :value="link" readonly @focus="($event.target as HTMLInputElement).select()" />
        </p>
        <button type="button" data-test="delete-household" @click="destroy">Delete household</button>
      </div>
      <button v-else type="button" data-test="leave" @click="leave">Leave household</button>
    </template>
  </section>
</template>

<style scoped>
.error {
  color: #e06c75;
}

input[readonly] {
  width: 100%;
}
</style>
