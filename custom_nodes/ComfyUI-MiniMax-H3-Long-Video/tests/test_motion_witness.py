import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('motion_witness', Path(__file__).resolve().parents[1] / 'minimax_h3_long_video/motion_witness.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MotionWitnessTests(unittest.TestCase):
    def observe(self, sequence):
        value = {'planned_events': [{'id': i, 'action': name} for i, name in enumerate(('walk', 'touch', 'look up'), 1)],
                 'observations': [{'frame': i, 'event_id': event, 'phase': phase, 'evidence': 'Visible state'}
                                  for i, (event, phase) in enumerate(sequence)]}
        return m.parse_motion_witness(json.dumps(value), len(sequence))

    def test_ongoing_gait_and_held_pose_are_not_replays(self):
        r = self.observe([(1, 'ongoing'), (1, 'ongoing'), (2, 'complete'), (3, 'complete'), (3, 'complete')])
        self.assertEqual(r['status'], 'confirmed_no_replay')

    def test_completed_sequence_restarts(self):
        r = self.observe([(1, 'ongoing'), (2, 'complete'), (3, 'complete'), (1, 'start'), (2, 'complete')])
        self.assertEqual(r['status'], 'confirmed_replay')
        self.assertEqual(r['replays'][0]['frame'], 3)

    def test_same_completed_gesture_restarts(self):
        self.assertEqual(self.observe([(3, 'start'), (3, 'complete'), (3, 'start')])['status'], 'confirmed_replay')

    def test_unreadable_samples_cannot_clear_a_claim(self):
        self.assertEqual(self.observe([(0, 'uncertain'), (1, 'ongoing')])['status'], 'unavailable')

    def test_uncertainty_cannot_erase_observed_reset(self):
        self.assertEqual(self.observe([(0, 'uncertain'), (3, 'complete'), (1, 'start')])['status'], 'confirmed_replay')

    def test_missing_or_out_of_order_observations_are_invalid(self):
        value = {'planned_events': [{'id': 1, 'action': 'walk'}], 'observations': [
            {'frame': 1, 'event_id': 1, 'phase': 'ongoing', 'evidence': 'Visible walking'}]}
        with self.assertRaises(ValueError): m.parse_motion_witness(json.dumps(value), 1)
        value['observations'][0]['frame'] = 0
        with self.assertRaises(ValueError): m.parse_motion_witness(json.dumps(value), 2)
        value['observations'][0]['event_id'] = 2
        with self.assertRaises(ValueError): m.parse_motion_witness(json.dumps(value), 1)


if __name__ == '__main__':
    unittest.main()
