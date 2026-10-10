import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'

import ServiceForm from '@/components/ServiceForm.vue'

it('emits a payload with blanks turned into nulls and numbers parsed', async () => {
  const wrapper = mount(ServiceForm, { props: { submitLabel: 'Add service' } })

  await wrapper.find('input[name="name"]').setValue('Internet')
  await wrapper.find('input[name="category"]').setValue('internet')
  await wrapper.find('input[name="expected_monthly_cost"]').setValue('79.99')
  await wrapper.find('input[name="auto_pay"]').setValue(true)
  await wrapper.find('form').trigger('submit')

  expect(wrapper.emitted('submit')![0][0]).toEqual({
    name: 'Internet',
    category: 'internet',
    account_number: null,
    contract_start: null,
    contract_end: null,
    renewal_reminder_days: null,
    expected_monthly_cost: '79.99',
    auto_pay: true,
    alert_threshold_pct: 20,
  })
})

it('starts from the service it is given', async () => {
  const wrapper = mount(ServiceForm, {
    props: {
      submitLabel: 'Save',
      initial: {
        name: 'Mobile', category: 'phone', alert_threshold_pct: 10, renewal_reminder_days: 30,
        contract_end: '2027-01-31',
      },
    },
  })

  expect((wrapper.find('input[name="name"]').element as HTMLInputElement).value).toBe('Mobile')
  await wrapper.find('form').trigger('submit')
  expect(wrapper.emitted('submit')![0][0]).toMatchObject({
    alert_threshold_pct: 10,
    renewal_reminder_days: 30,
    contract_end: '2027-01-31',
  })
})

it('does not emit without a name and a category', async () => {
  const wrapper = mount(ServiceForm, { props: { submitLabel: 'Add service' } })

  await wrapper.find('form').trigger('submit')

  expect(wrapper.emitted('submit')).toBeUndefined()
})
